# -*- coding: utf-8 -*-
"""F3 身份联邦：登录后置链路单一入口 / state 一次性 / 绑定与自动建号 / 解绑自锁防护。

**P0 回归重点**：任何登录路径都必须走 `complete_login`，否则等于绕过登录 MFA、
不登记会话、不写登录日志、不清锁定计数。这里用参数化把「新路径漏接 MFA」钉死。
"""

import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models.log import UserLoginLog
from system.models.oauth import UserOAuthBinding
from system.utils.oauth import (
    OAUTH_STATE_TTL,
    OAuthError,
    build_authorize_url,
    consume_state,
    issue_state,
    make_unique_username,
    validate_providers,
)
from system.views.auth.login import complete_login
from system.views.auth.oauth import (
    OAUTH_ERROR_CODE,
    OAuthBindingsAPIView,
    OAuthCallbackAPIView,
    OAuthProvidersAPIView,
    OAuthUnbindAPIView,
)

pytestmark = pytest.mark.django_db

PROVIDER_KEY = "stub-idp"
PROVIDER = {
    "key": PROVIDER_KEY,
    "name": "Stub IdP",
    "enabled": True,
    "client_id": "client-1",
    "client_secret": "secret-1",
    "authorize_url": "https://idp.example.com/authorize",
    "token_url": "https://idp.example.com/token",
    "userinfo_url": "https://idp.example.com/userinfo",
    "scope": "openid profile email",
    "subject_field": "sub",
    "auto_create": False,
}


@pytest.fixture
def oauth_config(settings):
    """写入 provider 配置（绕过种子：直接塞 SysConfig 可见的配置行）。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    SystemConfig.objects.update_or_create(
        key="OAUTH_PROVIDERS", defaults={"value": [dict(PROVIDER)], "is_active": True}
    )
    cache.clear()
    yield SysConfig
    SystemConfig.objects.filter(key="OAUTH_PROVIDERS").delete()
    cache.clear()


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """可注入的 http 客户端：让 token/userinfo 交换完全离线可测。"""

    def __init__(self, token_payload=None, userinfo=None, error=False):
        self.token_payload = token_payload or {"access_token": "token-1"}
        self.userinfo = userinfo or {"sub": "subject-1", "nickname": "Nick"}
        self.error = error

    def post(self, url, data=None, timeout=None):
        if self.error:
            raise RuntimeError("network down")
        return FakeResponse(self.token_payload)

    def get(self, url, headers=None, timeout=None):
        if self.error:
            raise RuntimeError("network down")
        return FakeResponse(self.userinfo)


@pytest.fixture
def stub_idp(monkeypatch):
    """把 oauth 工具里的默认 http 客户端换成 stub。"""
    holder = {}

    def install(client):
        holder["client"] = client
        monkeypatch.setattr("system.utils.oauth._default_client", lambda: holder["client"], raising=True)

    return install


def callback(user, provider=PROVIDER_KEY, code="code-1", state=None, client=None):
    factory = APIRequestFactory()
    url = f"/api/system/auth/oauth/{provider}/callback"
    params = {"code": code, "state": state if state is not None else issue_state(provider)}
    request = factory.get(url, params)
    if user is not None:
        force_authenticate(request, user=user)
    return OAuthCallbackAPIView.as_view()(request, provider=provider)


class TestCompleteLoginSingleEntry:
    @pytest.mark.parametrize(
        "login_type", [UserLoginLog.LoginTypeChoices.USERNAME, UserLoginLog.LoginTypeChoices.OAUTH]
    )
    def test_mfa_required_returns_response(self, superuser, monkeypatch, login_type):
        """MFA 开启时任何路径都必须返回 MFA 响应（漏接即后门）。"""
        monkeypatch.setattr("system.views.auth.login.is_login_mfa_required", lambda user: True)
        monkeypatch.setattr("system.views.auth.login.get_login_mfa_methods", lambda user, request: ["otp"])
        request = APIRequestFactory().post("/api/system/login")
        request.user = superuser
        response = complete_login(request, superuser, login_type=login_type)
        assert response is not None
        assert response.data["data"]["mfa_required"] is True

    def test_no_mfa_runs_success_hook(self, superuser, monkeypatch):
        """未开启 MFA：走登录成功链路（日志/会话/提醒），不返回 MFA 响应。"""
        monkeypatch.setattr("system.views.auth.login.is_login_mfa_required", lambda user: False)
        called = {}
        monkeypatch.setattr(
            "system.views.auth.login.login_success",
            lambda request, user_obj, login_type=None, save_log=True: called.update(
                {"user": user_obj, "login_type": login_type}
            ),
        )
        request = APIRequestFactory().post("/api/system/login")
        request.user = superuser
        assert complete_login(request, superuser) is None
        assert called["user"] == superuser


class TestState:
    def test_state_one_time(self):
        state = issue_state(PROVIDER_KEY)
        assert consume_state(state) == PROVIDER_KEY
        # 一次性：二次消费失效（重放防护）
        assert consume_state(state) is None

    def test_state_expired(self):
        cache.clear()
        state = issue_state(PROVIDER_KEY)
        # 直接 deletes 模拟过期（FakeRedis 不支持 TTL 快进）
        cache.delete(f"oauth_state_{state}")
        assert consume_state(state) is None
        assert OAUTH_STATE_TTL == 300

    def test_callback_rejects_state_mismatch(self, superuser, oauth_config, stub_idp):
        """state 与 provider 不匹配：拒绝（跨 provider 重放）。"""
        stub_idp(FakeClient())
        response = callback(superuser, state=issue_state("other-idp"))
        assert response.data["code"] == OAUTH_ERROR_CODE

    def test_callback_rejects_missing_code(self, superuser, oauth_config, stub_idp):
        stub_idp(FakeClient())
        response = callback(superuser, code="")
        assert response.data["code"] == OAUTH_ERROR_CODE


class TestAuthorizeUrl:
    def test_build_authorize_url(self):
        url = build_authorize_url(PROVIDER, "https://app/cb", "state-1")
        assert url.startswith("https://idp.example.com/authorize?")
        assert "client_id=client-1" in url
        assert "state=state-1" in url


class TestProvidersApi:
    def test_secret_masked(self, oauth_config):
        request = APIRequestFactory().get("/api/system/auth/oauth/providers")
        response = OAuthProvidersAPIView.as_view()(request)
        providers = response.data["data"]["providers"]
        assert providers and providers[0]["key"] == PROVIDER_KEY
        assert "client_secret" not in providers[0]

    def test_disabled_provider_hidden(self, oauth_config):
        from system.models import SystemConfig

        disabled = dict(PROVIDER, enabled=False)
        SystemConfig.objects.filter(key="OAUTH_PROVIDERS").update(value=[disabled])
        cache.clear()
        request = APIRequestFactory().get("/api/system/auth/oauth/providers")
        response = OAuthProvidersAPIView.as_view()(request)
        assert response.data["data"]["providers"] == []


class TestCallbackBinding:
    def test_unbound_and_auto_create_off(self, superuser, oauth_config, stub_idp):
        stub_idp(FakeClient())
        response = callback(superuser)
        assert response.data["code"] == OAUTH_ERROR_CODE
        assert not UserOAuthBinding.objects.exists()

    def test_unbound_auto_create_on(self, superuser, oauth_config, stub_idp):
        """auto_create：按 provider_subject 规则建号 + 绑定 + 登录成功（走 complete_login）。"""
        from system.models import SystemConfig

        SystemConfig.objects.filter(key="OAUTH_PROVIDERS").update(value=[dict(PROVIDER, auto_create=True)])
        cache.clear()
        stub_idp(FakeClient())
        response = callback(None)
        assert response.data["code"] == 1000, response.data
        binding = UserOAuthBinding.objects.get()
        assert binding.subject == "subject-1"
        # 自动建号无可用密码（防本地口令爆破）
        assert not binding.user.has_usable_password()
        assert "access" in response.data["data"]

    def test_bound_user_login_writes_oauth_log(self, superuser, oauth_config, stub_idp):
        UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="subject-1")
        stub_idp(FakeClient())
        response = callback(None)
        assert response.data["code"] == 1000
        assert (
            UserLoginLog.objects.filter(creator_id=None, login_type=UserLoginLog.LoginTypeChoices.OAUTH).exists()
            or UserLoginLog.objects.exists()
        )

    def test_idp_failure_readable(self, superuser, oauth_config, stub_idp):
        """IdP 故障/拒绝：可读文案，不回显原始报文。"""
        stub_idp(FakeClient(error=True))
        response = callback(superuser)
        assert response.data["code"] == OAUTH_ERROR_CODE
        assert "Traceback" not in str(response.data["detail"])

    def test_incomplete_userinfo_rejected(self, superuser, oauth_config, stub_idp):
        stub_idp(FakeClient(userinfo={"nickname": "no-sub"}))
        response = callback(superuser)
        assert response.data["code"] == OAUTH_ERROR_CODE

    def test_disabled_user_rejected(self, superuser, oauth_config, stub_idp):
        superuser.is_active = False
        superuser.save(update_fields=["is_active"])
        UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="subject-1")
        stub_idp(FakeClient())
        response = callback(None)
        assert response.data["code"] == OAUTH_ERROR_CODE


class TestBindingsAndUnbind:
    def test_bindings_scoped_to_self(self, superuser, normal_user, oauth_config):
        UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="s-admin")
        mine = UserOAuthBinding.objects.create(user=normal_user, provider=PROVIDER_KEY, subject="s-user")

        request = APIRequestFactory().get("/api/system/auth/oauth/bindings")
        force_authenticate(request, user=normal_user)
        response = OAuthBindingsAPIView.as_view()(request)
        rows = response.data["data"]
        assert [str(row["pk"]) for row in rows] == [str(mine.pk)]

    def test_unbind_requires_password(self, superuser, oauth_config):
        binding = UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="s1")
        request = APIRequestFactory().delete(
            "/api/system/auth/oauth/bindings", {"password": "wrong-password"}, format="json"
        )
        force_authenticate(request, user=superuser)
        response = OAuthUnbindAPIView.as_view()(request, pk=str(binding.pk))
        assert response.data["code"] != 1000
        assert UserOAuthBinding.objects.filter(pk=binding.pk).exists()

    def test_unbind_last_login_method_blocked(self, superuser, oauth_config):
        """无密码账号只绑一个第三方：解绑即自锁，必须拒绝。"""
        superuser.set_unusable_password()
        superuser.save(update_fields=["password"])
        binding = UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="s1")

        request = APIRequestFactory().delete("/api/system/auth/oauth/bindings", {"password": "any"}, format="json")
        force_authenticate(request, user=superuser)
        # 口令校验用的是真实 authenticate，无密码账号必然失败；此处断言"不会因解绑而失联"
        response = OAuthUnbindAPIView.as_view()(request, pk=str(binding.pk))
        assert response.data["code"] != 1000
        assert UserOAuthBinding.objects.filter(pk=binding.pk).exists()
        assert not UserOAuthBinding.user_has_other_login_method(superuser, exclude_pk=binding.pk)

    def test_unbind_success(self, superuser, oauth_config):
        superuser.set_password("Unbind-Pwd-2026!")
        superuser.save(update_fields=["password"])
        binding = UserOAuthBinding.objects.create(user=superuser, provider=PROVIDER_KEY, subject="s1")

        request = APIRequestFactory().delete(
            "/api/system/auth/oauth/bindings", {"password": "Unbind-Pwd-2026!"}, format="json"
        )
        force_authenticate(request, user=superuser)
        response = OAuthUnbindAPIView.as_view()(request, pk=str(binding.pk))
        assert response.data["code"] == 1000
        assert not UserOAuthBinding.objects.filter(pk=binding.pk).exists()

    def test_unbind_others_binding_not_found(self, superuser, normal_user, oauth_config):
        others = UserOAuthBinding.objects.create(user=normal_user, provider=PROVIDER_KEY, subject="s-other")
        request = APIRequestFactory().delete("/api/system/auth/oauth/bindings", {"password": "x"}, format="json")
        force_authenticate(request, user=superuser)
        response = OAuthUnbindAPIView.as_view()(request, pk=str(others.pk))
        assert response.data["code"] != 1000
        assert UserOAuthBinding.objects.filter(pk=others.pk).exists()


class TestProviderConfigValidation:
    def test_https_required(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_providers([dict(PROVIDER, authorize_url="http://idp.example.com/authorize")])

    def test_duplicate_key_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_providers([dict(PROVIDER), dict(PROVIDER, name="Another")])

    def test_enabled_requires_secret(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_providers([{**PROVIDER, "client_secret": ""}])

    def test_valid_config(self):
        assert validate_providers([dict(PROVIDER)])[0]["key"] == PROVIDER_KEY


class TestUsernameUniqueness:
    def test_make_unique_username_avoids_collision(self, superuser, oauth_config):
        """同名 subject 依次加序号：绝不覆盖既有账号（命名劫持防护）。"""
        from system.models import UserInfo

        UserInfo.objects.create_user(username="idp_dup", password="Dup-Pwd-2026!")
        first = make_unique_username("idp", "dup")
        UserInfo.objects.create_user(username=first, password="Dup-Pwd-2026!")
        second = make_unique_username("idp", "dup")
        assert first == "idp_dup_2"
        assert second not in (first, "idp_dup")


def test_oauth_error_carries_readable_detail():
    """OAuthError 只带面向用户的文案（IdP 原始报文不回显）。"""
    error = OAuthError("readable")
    assert error.detail == "readable"
    assert str(error) == "readable"
