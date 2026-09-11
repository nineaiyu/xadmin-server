# -*- coding: utf-8 -*-
"""个人访问令牌（PAT）：创建一次明文 + 哈希存储 + 认证链 + 吊销即时失效 + 清理。"""

import datetime
import json

import pytest
from django.core.cache import cache as django_cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from common.core.auth import PersonalAccessTokenAuthentication, path_allowed_by_scopes
from common.core.config import SysConfig
from common.core.permission import IsAuthenticated as ApiIsAuthenticated
from common.core.permission import PatScopePermission
from common.core.response import ApiResponse
from common.core.throttle import PatThrottle
from system.models import OperationLog
from system.models.token import PersonalAccessToken
from system.tasks import auto_clean_pat_job
from system.views.user.token import PersonalAccessTokenViewSet

pytestmark = pytest.mark.django_db

TOKENS_URL = "/api/system/personal-access-tokens"


def _create_token(user, name="ci-token", **kwargs):
    factory = APIRequestFactory()
    request = factory.post(TOKENS_URL, {"name": name, **kwargs}, format="json")
    force_authenticate(request, user=user)
    return PersonalAccessTokenViewSet.as_view({"post": "create"})(request)


def _auth_request(token, user=None):
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


def test_pat_list_search_filters(superuser, normal_user):
    """列表过滤：名称/前缀模糊 + 启用状态精确；个人取值域不因过滤后端放宽。"""
    mine = _create_token(superuser, name="alpha-token")
    other = _create_token(normal_user, name="alpha-other")
    mine_pk = str(mine.data["data"]["pk"])
    other_pk = str(other.data["data"]["pk"])

    def _list_pks(query):
        factory = APIRequestFactory()
        request = factory.get(TOKENS_URL, query)
        force_authenticate(request, user=superuser)
        response = PersonalAccessTokenViewSet.as_view({"get": "list"})(request)
        data = response.data["data"]
        results = data["results"] if isinstance(data, dict) else data
        return [str(item["pk"]) for item in results]

    # 模糊命中本人凭证，且他人同名凭证不出现在本人列表（个人取值域不被过滤放宽）
    pks = _list_pks({"name": "alpha"})
    assert mine_pk in pks
    assert other_pk not in pks

    assert _list_pks({"name": "no-such-token"}) == []
    assert _list_pks({"token_prefix": mine.data["data"]["token_prefix"]}) == [mine_pk]
    assert _list_pks({"is_active": "false"}) == []


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


# ---------------------------------------------------------------------------
# F4：scope 与调用审计 / 限流
# ---------------------------------------------------------------------------


class _ScopeProbeView(APIView):
    """scope 校验探针：真实走认证 + 权限链（限流关闭单独测）。"""

    authentication_classes = [PersonalAccessTokenAuthentication]
    permission_classes = [ApiIsAuthenticated, PatScopePermission]
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        return ApiResponse(data={"ok": True})


class _ScopeDefaultChainProbeView(APIView):
    """只挂 IsAuthenticated 的探针：模拟 action 级 permission_classes 覆写掉默认链。"""

    authentication_classes = [PersonalAccessTokenAuthentication]
    permission_classes = [ApiIsAuthenticated]
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        return ApiResponse(data={"ok": True})


def _probe(plain_token, path, view_cls=_ScopeProbeView):
    request = APIRequestFactory().get(path, HTTP_AUTHORIZATION=f"Pat {plain_token}")
    # DRF 异常处理器会 set_rollback：包独立 atomic 块，保持外层测试事务可用
    with transaction.atomic():
        return view_cls.as_view()(request)


@pytest.fixture(autouse=True)
def _restore_pat_rate_limit():
    """限流速率配置与节流历史按用例隔离，防止污染其他用例。"""
    yield
    SysConfig.set_value("PAT_RATE_LIMIT", "60/min")
    django_cache.clear()


class _MethodProbeView(APIView):
    """方法维度 scope 探针：同一路径 GET / POST 均可用。"""

    authentication_classes = [PersonalAccessTokenAuthentication]
    permission_classes = [ApiIsAuthenticated]
    throttle_classes = []

    def get(self, request, *args, **kwargs):
        return ApiResponse(data={"ok": True})

    def post(self, request, *args, **kwargs):
        return ApiResponse(data={"ok": True})


def test_scope_method_prefix_semantics_pure_function():
    """``METHOD /path`` 条目：只放行该方法的该路径；纯路径条目不受方法影响。"""
    scopes = ["GET /api/system/user", "/api/system/role"]
    assert path_allowed_by_scopes("/api/system/user", scopes, "GET") is True
    assert path_allowed_by_scopes("/api/system/user", scopes, "get") is True
    assert path_allowed_by_scopes("/api/system/user", scopes, "POST") is False
    # 无方法前缀的条目不限方法
    assert path_allowed_by_scopes("/api/system/role", scopes, "POST") is True
    # 请求方法未知时，方法限定条目不匹配（fail-closed）
    assert path_allowed_by_scopes("/api/system/user", ["GET /api/system/user"]) is False


def test_scope_method_prefix_enforced_in_request(superuser):
    """真实请求：``GET /api/system/user`` 放行 GET、拦住 POST。"""
    plain = _create_token(superuser, scopes=["GET /api/system/user"]).data["data"]["token"]
    assert _probe(plain, "/api/system/user", _MethodProbeView).status_code == 200
    request = APIRequestFactory().post("/api/system/user", HTTP_AUTHORIZATION=f"Pat {plain}")
    with transaction.atomic():
        response = _MethodProbeView.as_view()(request)
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_ip_allowed_by_allowlist_pure_function():
    """IP 白名单：空 = 不限；单 IP/CIDR 命中；非法条目跳过；客户端 IP 不可解析即拒绝。"""
    from common.core.auth import ip_allowed_by_allowlist

    assert ip_allowed_by_allowlist("127.0.0.1", []) is True
    assert ip_allowed_by_allowlist("127.0.0.1", ["127.0.0.1"]) is True
    assert ip_allowed_by_allowlist("127.0.0.2", ["127.0.0.1"]) is False
    assert ip_allowed_by_allowlist("10.1.2.3", ["10.0.0.0/8"]) is True
    assert ip_allowed_by_allowlist("192.168.1.1", ["10.0.0.0/8"]) is False
    # 非法条目跳过，其余条目照常生效
    assert ip_allowed_by_allowlist("127.0.0.1", ["not-an-ip", "127.0.0.1"]) is True
    assert ip_allowed_by_allowlist("127.0.0.1", ["not-an-ip"]) is False
    # 客户端 IP 不可解析（如 get_request_ip 兜底的 unknown）：fail-closed
    assert ip_allowed_by_allowlist("unknown", ["127.0.0.1"]) is False


def test_pat_ip_allowlist_blocks_authentication(superuser):
    """白名单未命中的凭证：认证阶段即拒绝（探针视图走真实认证链）。

    DRF 对无 WWW-Authenticate 挑战的认证失败统一返回 403（与 scope 越界同码）。
    """
    blocked = _create_token(superuser, ip_allowlist=["10.0.0.1"]).data["data"]["token"]
    request = APIRequestFactory().get("/api/system/user", HTTP_AUTHORIZATION=f"Pat {blocked}")
    with transaction.atomic():
        response = _ScopeProbeView.as_view()(request)
    assert response.status_code == status.HTTP_403_FORBIDDEN

    allowed = _create_token(superuser, name="allowed", ip_allowlist=["127.0.0.1"]).data["data"]["token"]
    assert _probe(allowed, "/api/system/user").status_code == 200


def test_ip_allowlist_crud_cleaning_via_api(superuser):
    """IP 白名单编辑：去空白/去重/丢弃空串；非法格式被拒；None = 不限。"""
    pk = _create_token(superuser).data["data"]["pk"]

    def _patch(payload):
        request = APIRequestFactory().patch(f"{TOKENS_URL}/{pk}", payload, format="json")
        force_authenticate(request, user=superuser)
        with transaction.atomic():
            return PersonalAccessTokenViewSet.as_view({"patch": "partial_update"})(request, pk=pk)

    response = _patch({"ip_allowlist": [" 127.0.0.1 ", "127.0.0.1", "", "10.0.0.0/8"]})
    assert response.data["code"] == 1000
    assert response.data["data"]["ip_allowlist"] == ["127.0.0.1", "10.0.0.0/8"]

    invalid = _patch({"ip_allowlist": ["127.0.0.1", "999.1.1.1"]})
    assert invalid.status_code == status.HTTP_400_BAD_REQUEST
    assert "999.1.1.1" in json.dumps(invalid.data, ensure_ascii=False)

    assert _patch({"ip_allowlist": None}).data["data"]["ip_allowlist"] == []


def test_path_allowed_by_scopes_pure_function():
    """纯函数口径：空清单放行；前缀/正则命中；非法正则跳过不 500。"""
    assert path_allowed_by_scopes("/api/system/user/1", []) is True
    assert path_allowed_by_scopes("/api/system/user/1", ["/api/system/user"]) is True
    assert path_allowed_by_scopes("/api/system/role", ["/api/system/user"]) is False
    assert path_allowed_by_scopes("/api/system/role", [r"/api/system/role$"]) is True
    # 非法正则被跳过：不匹配该条，但清单内其余条目照常生效
    assert path_allowed_by_scopes("/api/system/user", ["[invalid", "/api/system/user"]) is True
    assert path_allowed_by_scopes("/api/system/role", ["[invalid"]) is False


def test_scope_empty_allows_all_paths(superuser):
    """旧 token（无 scopes）行为不变：任意路径放行。"""
    plain = _create_token(superuser).data["data"]["token"]
    for path in ("/api/system/user", "/api/system/role", "/api/settings/basic"):
        assert _probe(plain, path).status_code == 200


def test_scope_prefix_allows_hit_and_blocks_out_of_scope(superuser):
    """scope 前缀命中放行、越界 403（scope 不做数据权限收窄，登记边界）。"""
    plain = _create_token(superuser, scopes=["/api/system/user"]).data["data"]["token"]
    assert _probe(plain, "/api/system/user").status_code == 200
    assert _probe(plain, "/api/system/user/1").status_code == 200
    assert _probe(plain, "/api/system/role").status_code == status.HTTP_403_FORBIDDEN


def test_scope_enforced_when_permission_classes_overridden(superuser):
    """显式覆写 permission_classes（只挂 IsAuthenticated）时 scope 仍生效。

    回归守护：DRF 的 action 级 permission_classes 会整体替换默认链，若 scope 校验
    写成独立权限类就会被漏掉（改密/解绑 MFA/重置 MFA 等入口正是这种写法）。
    """
    plain = _create_token(superuser, scopes=["/api/system/user"]).data["data"]["token"]
    assert _probe(plain, "/api/system/user", _ScopeDefaultChainProbeView).status_code == 200
    assert _probe(plain, "/api/system/role", _ScopeDefaultChainProbeView).status_code == status.HTTP_403_FORBIDDEN


def test_scope_invalid_regex_not_500(superuser):
    """scope 含非法正则：跳过该条不 500，其余条目照常生效。"""
    plain = _create_token(superuser, scopes=["[invalid", "/api/system/user"]).data["data"]["token"]
    assert _probe(plain, "/api/system/user").status_code == 200
    assert _probe(plain, "/api/system/role").status_code == status.HTTP_403_FORBIDDEN


def test_dual_header_jwt_plus_pat_scope_still_enforced(superuser):
    """同请求带 JWT + Pat 双 header：JWT 认证胜出（pat_scopes 未挂），scope 仍生效
    （PatScopePermission 从原始头补解析凭证，评审复盘 P1-4）。"""
    plain = _create_token(superuser, scopes=["/api/system/user"]).data["data"]["token"]
    request = APIRequestFactory().get("/api/system/role", HTTP_AUTHORIZATION=f"Pat {plain}")
    force_authenticate(request, user=superuser)  # 模拟 JWT 胜出：user 直挂、认证类不触发
    with transaction.atomic():
        response = _ScopeProbeView.as_view()(request)
    assert response.status_code == status.HTTP_403_FORBIDDEN

    request = APIRequestFactory().get("/api/system/user", HTTP_AUTHORIZATION=f"Pat {plain}")
    force_authenticate(request, user=superuser)
    assert _ScopeProbeView.as_view()(request).status_code == 200


class _ThrottleProbeView(APIView):
    authentication_classes = [PersonalAccessTokenAuthentication]
    permission_classes = [ApiIsAuthenticated]
    throttle_classes = [PatThrottle]

    def get(self, request, *args, **kwargs):
        return ApiResponse(data={"ok": True})


def test_pat_throttle_limit_and_unlimited(superuser):
    """限流阈值生效（超额 429）；0 = 不限；非 PAT 请求不受影响。"""
    plain = _create_token(superuser).data["data"]["token"]

    def _hit():
        request = APIRequestFactory().get("/api/system/user", HTTP_AUTHORIZATION=f"Pat {plain}")
        return _ThrottleProbeView.as_view()(request)

    SysConfig.set_value("PAT_RATE_LIMIT", "2/min")
    django_cache.clear()
    assert _hit().status_code == 200
    assert _hit().status_code == 200
    with transaction.atomic():
        response = _hit()
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # 0 = 不限（清空节流历史后验证）
    SysConfig.set_value("PAT_RATE_LIMIT", "0")
    django_cache.clear()
    for _ in range(4):
        assert _hit().status_code == 200

    # 限流按凭证隔离：换凭证不受上一凭证历史影响（token_hash 独立 key）
    SysConfig.set_value("PAT_RATE_LIMIT", "1/min")
    django_cache.clear()
    plain2 = _create_token(superuser, name="second").data["data"]["token"]
    request = APIRequestFactory().get("/api/system/user", HTTP_AUTHORIZATION=f"Pat {plain2}")
    assert _ThrottleProbeView.as_view()(request).status_code == 200

    # 非 PAT 请求（JWT 会话）不经过 PAT 限流
    django_cache.clear()
    request = APIRequestFactory().get("/api/system/user")
    force_authenticate(request, user=superuser)
    assert _ThrottleProbeView.as_view()(request).status_code == 200


def test_scopes_crud_cleaning_via_api(superuser):
    """scope 编辑：清洗空白/去重/丢弃空串；None = 不限。"""
    response = _create_token(superuser)
    pk = response.data["data"]["pk"]

    factory = APIRequestFactory()
    request = factory.patch(
        f"{TOKENS_URL}/{pk}",
        {"scopes": [" /api/system/user ", "/api/system/user", "", "/api/system/role"]},
        format="json",
    )
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"patch": "partial_update"})(request, pk=pk)
    assert response.data["code"] == 1000
    assert response.data["data"]["scopes"] == ["/api/system/user", "/api/system/role"]

    request = APIRequestFactory().patch(f"{TOKENS_URL}/{pk}", {"scopes": None}, format="json")
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"patch": "partial_update"})(request, pk=pk)
    assert response.data["data"]["scopes"] == []


def _create_operation_log(user, path="/api/system/user", status_code=1000, token_pk=None):
    return OperationLog.objects.create(
        creator=user,
        module="Probe",
        path=path,
        method="GET",
        status_code=status_code,
        token_pk=token_pk,
        auth_type=OperationLog.AuthType.PAT if token_pk else None,
    )


def test_operation_log_keeps_token_pk_after_token_deleted(superuser):
    """凭证删除后审计不断链：日志仍持有 token_pk 且可查（故刻意不建 FK）。"""
    pk = _create_token(superuser).data["data"]["pk"]
    log = _create_operation_log(superuser, token_pk=pk)
    PersonalAccessToken.objects.filter(pk=pk).delete()

    log.refresh_from_db()
    assert str(log.token_pk) == str(pk)
    assert log.auth_type == OperationLog.AuthType.PAT


def test_logs_and_stats_scoped_by_token_pk(superuser, normal_user):
    """调用记录/统计：精确口径——同一用户的多个凭证互不混算，无凭证标识的历史行不计入。"""
    first = _create_token(superuser).data["data"]["pk"]
    second = _create_token(superuser, name="second").data["data"]["pk"]
    _create_operation_log(superuser, token_pk=first)
    _create_operation_log(superuser, path="/api/system/role", status_code=1001, token_pk=first)
    _create_operation_log(superuser, token_pk=second)  # 另一凭证的调用，不得混算
    _create_operation_log(superuser)  # 升级前历史行（无凭证标识），不可区分 → 不计入

    factory = APIRequestFactory()
    request = factory.get(f"{TOKENS_URL}/{first}/logs")
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"get": "logs"})(request, pk=first)
    assert response.data["code"] == 1000
    data = response.data["data"]
    assert data["total"] == 2
    assert {item["path"] for item in data["results"]} == {"/api/system/user", "/api/system/role"}
    assert all(item["creator"]["pk"] == superuser.pk for item in data["results"])

    # 另一凭证只见自己的那 1 条
    request = APIRequestFactory().get(f"{TOKENS_URL}/{second}/logs")
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"get": "logs"})(request, pk=second)
    assert response.data["data"]["total"] == 1

    # stats：近 7 天 total=2、失败（status_code != 1000）=1
    request = APIRequestFactory().get(f"{TOKENS_URL}/{first}/stats")
    force_authenticate(request, user=superuser)
    response = PersonalAccessTokenViewSet.as_view({"get": "stats"})(request, pk=first)
    assert response.data["code"] == 1000
    assert response.data["data"]["total"] == 2
    assert response.data["data"]["failed"] == 1

    # 他人凭证：取值域保护（项目 Http404 统一转业务 400：地址错误或数据权限不允许）
    other = _create_token(normal_user).data["data"]["pk"]
    request = APIRequestFactory().get(f"{TOKENS_URL}/{other}/logs")
    force_authenticate(request, user=superuser)
    with transaction.atomic():
        response = PersonalAccessTokenViewSet.as_view({"get": "logs"})(request, pk=other)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == 400
