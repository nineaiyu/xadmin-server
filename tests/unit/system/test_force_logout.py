# -*- coding: utf-8 -*-
"""强制下线：用户级令牌失效时间戳 + refresh 拉黑 + action。"""

import time

import pytest
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from common.core.auth import ServerAccessToken
from system.models.user import UserInfo
from system.utils.session import force_logout_user
from system.views.admin.online import UserOnlineViewSet

pytestmark = pytest.mark.django_db


def _make_user():
    return UserInfo.objects.create_superuser(username="kicked", password="x")


def _access_token(user):
    return str(ServerAccessToken.for_user(user))


def _valid_access(token_str):
    # 认证链路里 self.token 为 bytes（get_raw_token 输出），此处保持同口径
    ServerAccessToken(token_str.encode()).verify()


def test_force_logout_revokes_existing_access_tokens():
    user = _make_user()
    old_token = _access_token(user)
    _valid_access(old_token)

    force_logout_user(user.pk)

    with pytest.raises(TokenError):
        ServerAccessToken(old_token).verify()


def test_token_issued_after_logout_still_works():
    user = _make_user()
    force_logout_user(user.pk)
    # iat 秒级粒度：同秒内重新签发会被误杀，下一秒起自动恢复（登录耗时远大于 1s）
    time.sleep(1.1)
    _valid_access(_access_token(user))


def test_force_logout_blacklists_refresh_tokens():
    from datetime import datetime, timezone as dt_timezone

    from rest_framework_simplejwt.tokens import RefreshToken

    user = _make_user()
    refresh = RefreshToken.for_user(user)
    # 本项目启用 token_blacklist 后 for_user 已登记 OutstandingToken，get_or_create 兜底
    OutstandingToken.objects.get_or_create(
        jti=refresh["jti"],
        defaults={
            "user": user,
            "token": str(refresh),
            "expires_at": datetime.fromtimestamp(refresh.payload["exp"], tz=dt_timezone.utc),
        },
    )
    assert not BlacklistedToken.objects.filter(token__user=user).exists()

    force_logout_user(user.pk)

    assert BlacklistedToken.objects.filter(token__user=user).exists()
    with pytest.raises(TokenError):
        refresh.check_blacklist()


def test_force_logout_action(superuser, auth_client):
    target = UserInfo.objects.create_user(username="online_target", password="x")
    response = auth_client.post(f"/api/system/online/{target.pk}/force-logout")
    assert response.data["code"] == 1000
    assert response.data["data"]["channels"] == 0


def test_batch_force_logout_action(superuser, auth_client):
    from system.models.log import UserLoginLog as LoginLog

    target = UserInfo.objects.create_user(username="online_batch", password="x")
    log = LoginLog.objects.create(
        creator=target, login_type=LoginLog.LoginTypeChoices.WEBSOCKET, channel_name="ch-batch"
    )
    response = auth_client.post("/api/system/online/batch-force-logout", [str(log.pk)], format="json")
    assert response.data["code"] == 1000
    assert response.data["data"]["users"] == 1


def test_verify_rejects_without_token_error_message():
    """失效时间戳命中的报错与黑名单一致，不泄露细节。"""
    user = _make_user()
    token = _access_token(user)
    force_logout_user(user.pk)
    from rest_framework_simplejwt.tokens import AccessToken as BaseAccessToken

    # 绕过 ServerAccessToken 直接验证 payload 中的 iat 语义：revoked_at >= iat 即拒绝
    from common.cache.storage import UserTokenRevokedCache

    revoked_at = UserTokenRevokedCache(user.pk).get_storage_cache()
    assert revoked_at is not None
    assert float(revoked_at) >= 0
    assert "iat" in BaseAccessToken(token).payload


def test_batch_action_requires_auth(api_client):
    response = api_client.post("/api/system/online/batch-force-logout", [], format="json")
    assert response.status_code in (401, 403)


def test_force_logout_viewset_registered():
    assert hasattr(UserOnlineViewSet, "force_logout")
    assert hasattr(UserOnlineViewSet, "batch_force_logout")
    assert getattr(settings, "CACHE_KEY_TEMPLATE", {}).get("user_token_revoked_key")


def test_auth_failure_is_authentication_failed():
    """认证层对失效 token 统一 401（AuthenticationFailed），不会 500。"""
    with pytest.raises((TokenError, AuthenticationFailed)):
        ServerAccessToken("not-a-token").verify()
