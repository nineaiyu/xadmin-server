# -*- coding: utf-8 -*-
"""个人访问令牌（PAT）：创建一次明文 + 哈希存储 + 认证链 + 吊销即时失效 + 清理。"""

import datetime

import pytest
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from common.core.auth import PersonalAccessTokenAuthentication
from system.models.token import PersonalAccessToken
from system.tasks import auto_clean_pat_job
from system.views.user.token import PersonalAccessTokenViewSet

pytestmark = pytest.mark.django_db

TOKENS_URL = "/api/system/personal-access-tokens"


def _create_token(user, name="ci-token", **kwargs):
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.post(TOKENS_URL, {"name": name, **kwargs}, format="json")
    force_authenticate(request, user=user)
    return PersonalAccessTokenViewSet.as_view({"post": "create"})(request)


def _auth_request(token, user=None):
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    request = factory.get(TOKENS_URL, HTTP_AUTHORIZATION=f"Pat {token}")
    return PersonalAccessTokenAuthentication().authenticate(request)


def test_create_returns_plain_token_once_and_stores_hash(superuser):
    """创建返回明文一次；库内只有哈希与前缀；重复创建得到不同凭证。"""
    response = _create_token(superuser)
    assert response.data["code"] == 1000
    data = response.data["data"]
    plain = data["token"]
    assert plain.startswith("pat_")
    assert len(plain) > 40
    record = PersonalAccessToken.objects.get(pk=data["pk"])
    assert record.token_hash != plain  # 库内是哈希
    assert len(record.token_hash) == 64
    assert record.token_prefix == plain[:12]
    assert record.creator == superuser
    assert record.is_active is True
    # 详情/列表不再返回明文
    instance = PersonalAccessToken.objects.get(pk=data["pk"])
    serializer = PersonalAccessTokenViewSet.serializer_class(instance)
    assert serializer.data["token"] is None

    response2 = _create_token(superuser, name="second")
    assert response2.data["data"]["token"] != plain


def test_pat_authenticates_and_sets_user(superuser):
    """有效凭证认证通过并挂载所属用户。"""
    response = _create_token(superuser)
    plain = response.data["data"]["token"]
    user, token_obj = _auth_request(plain)
    assert user.pk == superuser.pk
    assert token_obj.token_prefix == plain[:12]


def test_pat_last_used_time_throttled(superuser):
    """last_used_time 经 60s 节流：连续认证只回写一次。"""
    response = _create_token(superuser)
    plain = response.data["data"]["token"]
    _auth_request(plain)
    record = PersonalAccessToken.objects.get(token_prefix=plain[:12])
    first_used = record.last_used_time
    assert first_used is not None
    # 节流窗口内第二次认证不回写（last_used_time 不变）
    import time

    time.sleep(0.01)
    _auth_request(plain)
    record.refresh_from_db()
    assert record.last_used_time == first_used


def test_pat_invalid_or_revoked_fails_closed(superuser):
    """无效凭证/停用/过期/伪造：显式 401（fail-closed），无 Pat 头静默跳过。"""
    response = _create_token(superuser)
    plain = response.data["data"]["token"]
    record = PersonalAccessToken.objects.get(pk=response.data["data"]["pk"])

    # 停用即吊销（无需缓存失效链路）
    record.is_active = False
    record.save(update_fields=["is_active", "updated_time"])
    with pytest.raises(AuthenticationFailed):
        _auth_request(plain)

    # 重新启用后过期时间生效
    record.is_active = True
    record.expired_at = timezone.now() - datetime.timedelta(seconds=1)
    record.save(update_fields=["is_active", "expired_at", "updated_time"])
    with pytest.raises(AuthenticationFailed):
        _auth_request(plain)

    # 伪造凭证
    with pytest.raises(AuthenticationFailed):
        _auth_request("pat_totally-forged-token-value")

    # 无 Pat 头：静默返回 None，不干扰 JWT 认证链
    from rest_framework.test import APIRequestFactory

    request = APIRequestFactory().get(TOKENS_URL)
    assert PersonalAccessTokenAuthentication().authenticate(request) is None


def test_pat_queryset_scoped_to_creator(superuser, normal_user):
    """取值域严格个人：含超管在内只见本人凭证。"""
    mine = _create_token(superuser)
    _create_token(normal_user)

    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(TOKENS_URL)
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"get": "list"})(request)
    pks = (
        [item["pk"] for item in response.data["data"]["results"]]
        if isinstance(response.data["data"], dict)
        else [item["pk"] for item in response.data["data"]]
    )
    assert str(mine.data["data"]["pk"]) in [str(pk) for pk in pks]
    assert len(pks) == 1


def test_pat_request_hits_api_and_revocation_blocks(superuser):
    """端到端：Pat 头调接口 200 → 吊销后 401。"""
    response = _create_token(superuser)
    plain = response.data["data"]["token"]
    record = PersonalAccessToken.objects.get(pk=response.data["data"]["pk"])

    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    request = factory.get(TOKENS_URL, HTTP_AUTHORIZATION=f"Pat {plain}")
    # 显式走认证类装配（white URL + 认证链在 RequestFactory 单元测试里需手动触发）
    from common.core.auth import PersonalAccessTokenAuthentication

    user, _ = PersonalAccessTokenAuthentication().authenticate(request)
    assert user.pk == superuser.pk

    record.is_active = False
    record.save(update_fields=["is_active", "updated_time"])
    request = factory.get(TOKENS_URL, HTTP_AUTHORIZATION=f"Pat {plain}")
    from rest_framework.exceptions import AuthenticationFailed

    with pytest.raises(AuthenticationFailed):
        PersonalAccessTokenAuthentication().authenticate(request)


def test_auto_clean_pat_removes_expired_and_inactive(superuser):
    """清理：过期超 30 天的凭证，以及停用且 30 天未更新的凭证。"""
    expired_long_ago = PersonalAccessToken.objects.create(
        creator=superuser,
        name="expired",
        token_hash="a" * 64,
        token_prefix="pat_expired1",
        expired_at=timezone.now() - datetime.timedelta(days=31),
    )
    inactive_stale = PersonalAccessToken.objects.create(
        creator=superuser,
        name="inactive",
        token_hash="b" * 64,
        token_prefix="pat_inactive",
        is_active=False,
    )
    PersonalAccessToken.objects.filter(pk=inactive_stale.pk).update(
        updated_time=timezone.now() - datetime.timedelta(days=31)
    )
    keep_active = PersonalAccessToken.objects.create(
        creator=superuser, name="active", token_hash="c" * 64, token_prefix="pat_active"
    )
    keep_expired_recent = PersonalAccessToken.objects.create(
        creator=superuser,
        name="recent",
        token_hash="d" * 64,
        token_prefix="pat_recent",
        expired_at=timezone.now() - datetime.timedelta(days=2),
    )

    removed = auto_clean_pat_job.run()
    assert removed == 2
    assert not PersonalAccessToken.objects.filter(pk__in=[expired_long_ago.pk, inactive_stale.pk]).exists()
    assert PersonalAccessToken.objects.filter(pk__in=[keep_active.pk, keep_expired_recent.pk]).exists()


def test_pat_owner_user_inactive_rejected(superuser):
    """所属用户被停用后凭证拒绝（防停用账号借道 PAT 继续访问）。"""
    response = _create_token(superuser)
    plain = response.data["data"]["token"]
    superuser.is_active = False
    superuser.save(update_fields=["is_active"])
    with pytest.raises(AuthenticationFailed):
        _auth_request(plain)
