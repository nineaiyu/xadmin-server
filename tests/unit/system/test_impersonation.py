# -*- coding: utf-8 -*-
"""用户模拟：签发带 imp claim 的 token + 退出重签 + 会话/审计留痕 + 权限门禁。"""

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from audit.models import OperationLog
from audit.models.log import UserLoginLog
from common.core.auth import ServerAccessToken
from identity.models import UserSession
from identity.models.user import UserInfo
from system.models import DataPermission

pytestmark = pytest.mark.django_db

IMPERSONATE_PATH = "api/system/user/(?P<pk>[^/.]+)/impersonate$"


def _make_user(username):
    return UserInfo.objects.create_user(username=username, password="x")


def _confirm_password(api_client, user, password):
    """通过敏感操作密码二次确认（UserConfirmation.require(PASSWORD) 门禁）。"""
    response = api_client.post(
        "/api/mfa/confirm", {"confirm_type": "password", "method": "password", "code": password}, format="json"
    )
    assert response.data["code"] == 1000, response.data


def _granted_client(api_client, superuser):
    """已认证 + 已通过密码二次确认的超管客户端（模拟入口的完整前置）。"""
    api_client.force_authenticate(user=superuser)
    _confirm_password(api_client, superuser, "Admin@123456")
    return api_client


def _jwt_client(api_client, user):
    """真实 JWT 认证的客户端（读取 imp claim 的链路必须走真 token，不能用 force_authenticate）。"""
    token = str(RefreshToken.for_user(user).access_token)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


def _impersonate(api_client, target):
    return api_client.post(f"/api/system/user/{target.pk}/impersonate")


def _bearer_client(access_token: str) -> APIClient:
    """以模拟态 access token 发请求的独立客户端（force_authenticate 不会覆盖 Bearer 头）。"""
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    return client


# ---------------------------------------------------------------- 开始模拟


def test_superuser_impersonates_normal_user(auth_client, superuser, normal_user):
    _granted_client(auth_client, superuser)
    response = _impersonate(auth_client, normal_user)
    assert response.data["code"] == 1000
    data = response.data["data"]
    assert data["access"] and data["refresh"]

    payload = AccessToken(data["access"]).payload
    assert payload["imp"] == str(superuser.pk)

    # 模拟会话按 IMPERSONATE 类型登记（在线用户可见、单会话强退可达）
    assert UserSession.objects.filter(
        creator=normal_user, login_type=UserLoginLog.LoginTypeChoices.IMPERSONATE
    ).exists()
    # 登录日志留痕（归属被模拟用户）
    assert UserLoginLog.objects.filter(
        creator=normal_user, login_type=UserLoginLog.LoginTypeChoices.IMPERSONATE
    ).exists()
    # 显式操作日志留痕（归属模拟发起人）
    log = OperationLog.objects.filter(module="User:impersonate", object_pk=str(normal_user.pk)).first()
    assert log is not None and log.creator_id == superuser.pk


def test_impersonate_requires_password_confirmation(auth_client, superuser, normal_user):
    """未经密码二次确认直接发起模拟 → 412 user_confirm_required（前端引导确认后重发）。"""
    auth_client.force_authenticate(user=superuser)
    response = _impersonate(auth_client, normal_user)
    assert response.status_code == 412
    assert response.data["type"] == "user_confirm_required"


def test_impersonate_rejects_superuser_target(auth_client, superuser):
    _granted_client(auth_client, superuser)
    target = UserInfo.objects.create_superuser(username="big_boss", password="x")
    response = _impersonate(auth_client, target)
    assert response.data["code"] == 1001


def test_impersonate_rejects_self(auth_client, superuser):
    _granted_client(auth_client, superuser)
    response = _impersonate(auth_client, superuser)
    assert response.data["code"] == 1001


def test_impersonate_rejects_disabled_user(auth_client, superuser):
    _granted_client(auth_client, superuser)
    target = _make_user("disabled_target")
    target.is_active = False
    target.save(update_fields=["is_active"])
    response = _impersonate(auth_client, target)
    assert response.data["code"] == 1001


def test_impersonate_requires_menu_permission(api_client):
    """无 impersonate 权限点的用户 403（菜单权限校验，非仅前端隐藏）。"""
    actor = _make_user("no_perm_actor")
    target = _make_user("no_perm_target")
    api_client.force_authenticate(user=actor)
    response = _impersonate(api_client, target)
    assert response.status_code == 403


def test_role_user_with_grant_can_impersonate(api_client, normal_user, role, menu_factory):
    """授权闭环：角色绑定 impersonate 权限点 + 全部数据授权 → 可模拟其他用户。"""
    menu = menu_factory("impersonate:SystemUser", path=IMPERSONATE_PATH, method="POST")
    role.menu.add(menu)
    grant = DataPermission.objects.create(
        name="模拟-全部数据",
        rules=[{"table": "identity.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
    )
    normal_user.rules.add(grant)
    django_cache.clear()  # 权限缓存 24h：授权变更后需失效再取

    target = _make_user("grant_target")
    api_client.force_authenticate(user=normal_user)
    _confirm_password(api_client, normal_user, "Test@123456")
    response = _impersonate(api_client, target)
    assert response.data["code"] == 1000
    assert AccessToken(response.data["data"]["access"]).payload["imp"] == str(normal_user.pk)


def test_cannot_impersonate_while_impersonating(api_client, superuser, normal_user, role, menu_factory):
    """模拟态 token 再发起模拟被拒（防链式嵌套）；无权限点的模拟态用户在权限层已被 403。"""
    menu = menu_factory("impersonate:SystemUser", path=IMPERSONATE_PATH, method="POST")
    role.menu.add(menu)
    normal_user.rules.add(
        DataPermission.objects.create(
            name="链式-全部数据",
            rules=[{"table": "identity.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
        )
    )
    django_cache.clear()

    _granted_client(api_client, superuser)
    access = _impersonate(api_client, normal_user).data["data"]["access"]
    chain_client = _bearer_client(access)
    _confirm_password(chain_client, normal_user, "Test@123456")
    other = _make_user("chain_target")
    response = _impersonate(chain_client, other)
    assert response.data["code"] == 1001


# ---------------------------------------------------------------- 模拟态识别与退出


def test_userinfo_reports_impersonator(api_client, superuser, normal_user):
    _granted_client(api_client, superuser)
    access = _impersonate(api_client, normal_user).data["data"]["access"]
    response = _bearer_client(access).get("/api/system/userinfo")
    assert response.data["code"] == 1000
    assert response.data["data"]["impersonator"]["username"] == superuser.username


def test_userinfo_without_impersonation_has_no_impersonator(api_client, normal_user):
    response = _jwt_client(api_client, normal_user).get("/api/system/userinfo")
    assert response.data["code"] == 1000
    assert "impersonator" not in response.data["data"]


def test_exit_returns_impersonator_tokens_and_revokes_impersonated(api_client, superuser, normal_user):
    _granted_client(api_client, superuser)
    data = _impersonate(api_client, normal_user).data["data"]
    access, refresh = data["access"], data["refresh"]
    # 退出前取 jti（退出后 token 已入黑名单，再构造 RefreshToken 校验会直接抛错）
    refresh_jti = RefreshToken(refresh).payload["jti"]

    response = _bearer_client(access).post("/api/system/impersonate/exit", {"refresh": refresh}, format="json")
    assert response.data["code"] == 1000
    restored = response.data["data"]
    # 返回的是模拟发起人的新 token（payload 无 imp claim）
    restored_payload = AccessToken(restored["access"]).payload
    assert "imp" not in restored_payload
    assert restored_payload["user_id"] == str(superuser.pk)

    # 模拟态 access 立即失效、refresh 进入黑名单（for_user 在附加 imp/sid claim
    # 前登记 OutstandingToken，黑名单按 jti 关联，token 串是旧快照属正常）
    with pytest.raises(TokenError):
        ServerAccessToken(access.encode()).verify()
    outstanding = OutstandingToken.objects.get(jti=refresh_jti)
    assert BlacklistedToken.objects.filter(token=outstanding).exists()
    # 模拟会话置离线
    session_pk = AccessToken(access).payload.get("sid")
    assert UserSession.objects.get(pk=session_pk).status == UserSession.Status.OFFLINE


def test_exit_requires_impersonation_state(api_client, normal_user):
    response = _jwt_client(api_client, normal_user).post("/api/system/impersonate/exit", {}, format="json")
    assert response.data["code"] == 1001


def test_exit_without_menu_permission_still_works(api_client, superuser, normal_user):
    """退出模拟是安全阀：被模拟用户未必有任何菜单权限（白名单 URL），退出必须无条件可达。"""
    _granted_client(api_client, superuser)
    access = _impersonate(api_client, normal_user).data["data"]["access"]
    assert _bearer_client(access).post("/api/system/impersonate/exit", {}, format="json").data["code"] == 1000
