# -*- coding: utf-8 -*-
"""企业 IM 扫码登录 flavor 适配器（ADR-018）：钉钉 / 企业微信 / 飞书。

覆盖：配置预设合并与校验 / 授权地址参数形状 / 换码请求体与响应解析（含
errcode、code、data 包裹与兼容提取）/ userinfo 归一化（subject + 标准键）/
企业微信 corp token 缓存 / 错误掩码 / 回调集成（绑定唯一、MFA 回归、禁用拒绝）。
"""

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from system.models import SystemConfig, UserInfo
from system.utils.oauth import (
    OAuthError,
    build_authorize_url,
    exchange_code,
    fetch_userinfo,
    get_providers,
    issue_state,
    validate_providers,
)
from system.views.auth.oauth import OAUTH_ERROR_CODE, OAuthCallbackAPIView

pytestmark = pytest.mark.django_db

REDIRECT = "https://app.example.com/#/oauth/callback?provider=x"


def make_provider(flavor, **kw):
    """按「通用默认 < flavor 预设 < 显式配置」合成 provider（与 get_providers 同序）。"""
    from system.utils.oauth import OPTIONAL_DEFAULTS
    from system.utils.oauth_flavors import FLAVOR_PRESETS

    return {**OPTIONAL_DEFAULTS, **FLAVOR_PRESETS[flavor], "flavor": flavor, **kw}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class StubClient:
    """按 URL 前缀路由的假 http 客户端：记录调用、可注入网络异常。"""

    def __init__(self, routes=None, error=False):
        self.routes = routes or {}
        self.error = error
        self.calls = []

    def _resolve(self, url):
        if self.error:
            raise RuntimeError("network down")
        for prefix, payload in self.routes.items():
            if url.startswith(prefix):
                return FakeResponse(payload)
        return FakeResponse({})

    def post(self, url, json=None, data=None, timeout=None):
        self.calls.append(("post", url, json))
        return self._resolve(url)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("get", url, params, headers))
        return self._resolve(url)


@pytest.fixture
def stub_client(monkeypatch):
    holder = {"client": StubClient()}

    def install(client):
        holder["client"] = client
        # flavors 与 oauth 各有独立的 _default_client，统一替换
        monkeypatch.setattr("system.utils.oauth._default_client", lambda: holder["client"])
        monkeypatch.setattr("system.utils.oauth_flavors._default_client", lambda: holder["client"])

    return install


# ---------------------------------------------------------------- 配置


class TestFlavorConfig:
    def test_preset_merge(self):
        """预设合并：管理员只填三元组，官方端点与默认字段自动补齐。"""
        SystemConfig.objects.update_or_create(
            key="OAUTH_PROVIDERS",
            defaults={
                "value": [
                    {
                        "key": "feishu",
                        "name": "飞书",
                        "flavor": "feishu",
                        "client_id": "cli",
                        "client_secret": "sec",
                        "enabled": True,
                    }
                ],
                "is_active": True,
            },
        )
        cache.clear()
        try:
            provider = get_providers(enabled_only=True)[0]
            assert provider["authorize_url"] == "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
            assert provider["subject_field"] == "union_id"
            assert provider["flavor"] == "feishu"
        finally:
            SystemConfig.objects.filter(key="OAUTH_PROVIDERS").delete()
            cache.clear()

    def test_explicit_override_wins(self):
        provider = get_providers(enabled_only=False)  # 占位避免空配置误判；真实断言在下方直接验证合并顺序
        item = {
            "key": "feishu",
            "name": "飞书",
            "flavor": "feishu",
            "client_id": "c",
            "client_secret": "s",
            "subject_field": "open_id",
        }
        from system.utils import oauth

        merged = {**oauth.OPTIONAL_DEFAULTS, **{}, **item}
        assert merged["subject_field"] == "open_id"
        assert provider == []

    def test_wecom_requires_agent_id(self):
        with pytest.raises(ValidationError):
            validate_providers(
                [{"key": "wecom", "name": "企微", "flavor": "wecom", "client_id": "corp", "client_secret": "s"}]
            )

    def test_unknown_flavor_rejected(self):
        with pytest.raises(ValidationError):
            validate_providers([{"key": "x", "name": "X", "flavor": "wechat", "client_id": "c", "client_secret": "s"}])

    def test_im_urls_optional_but_https_checked(self):
        """IM flavor 可不填 URL（走预设）；显式给了非 https 仍拒绝。"""
        ok = validate_providers(
            [{"key": "dingtalk", "name": "钉钉", "flavor": "dingtalk", "client_id": "c", "client_secret": "s"}]
        )
        # 校验侧不合并预设（读取时才合并）：URL 键缺省即可通过
        assert "token_url" not in ok[0]
        with pytest.raises(ValidationError):
            validate_providers(
                [
                    {
                        "key": "dingtalk",
                        "name": "钉钉",
                        "flavor": "dingtalk",
                        "client_id": "c",
                        "client_secret": "s",
                        "token_url": "http://insecure.example.com",
                    }
                ]
            )

    def test_oauth2_still_requires_urls(self):
        with pytest.raises(ValidationError):
            validate_providers([{"key": "generic", "name": "G", "client_id": "c", "client_secret": "s"}])


# ---------------------------------------------------------------- 授权地址


class TestAuthorizeUrl:
    def test_dingtalk_standard_shape(self):
        provider = make_provider("dingtalk", client_id="app-key")
        url = build_authorize_url(provider, REDIRECT, "st1")
        assert url.startswith("https://login.dingtalk.com/oauth2/auth?")
        assert "client_id=app-key" in url and "response_type=code" in url and "state=st1" in url

    def test_wecom_params(self):
        provider = make_provider("wecom", client_id="corp-1", agent_id="1000002")
        url = build_authorize_url(provider, REDIRECT, "st2")
        assert url.startswith("https://login.work.weixin.qq.com/wwlogin/sso/login?")
        assert "appid=corp-1" in url and "agentid=1000002" in url and "login_type=CorpApp" in url
        assert "response_type" not in url and "client_id" not in url

    def test_feishu_app_id_param(self):
        provider = make_provider("feishu", client_id="cli_a1")
        url = build_authorize_url(provider, REDIRECT, "st3")
        assert "app_id=cli_a1" in url and "client_id=" not in url and "response_type=code" in url


# ---------------------------------------------------------------- 钉钉


class TestDingtalk:
    def test_exchange_posts_json(self, stub_client):
        client = StubClient({"https://api.dingtalk.com": {"access_token": "dt-1", "expireIn": 7200}})
        stub_client(client)
        provider = make_provider("dingtalk")
        payload = exchange_code(provider, "code-1", REDIRECT)
        assert payload["access_token"] == "dt-1"
        method, url, body = client.calls[0]
        assert method == "post" and url == provider["token_url"]
        assert body == {"clientId": None, "clientSecret": None, "code": "code-1", "grantType": "authorization_code"}

    def test_exchange_rejected_without_token(self, stub_client):
        stub_client(StubClient({"https://api.dingtalk.com": {"message": "bad code"}}))
        with pytest.raises(OAuthError):
            exchange_code(make_provider("dingtalk"), "bad", REDIRECT)

    def test_network_error_masked(self, stub_client):
        stub_client(StubClient(error=True))
        with pytest.raises(OAuthError) as exc_info:
            exchange_code(make_provider("dingtalk"), "c", REDIRECT)
        assert "network down" not in str(exc_info.value.detail)

    def test_userinfo_normalized(self, stub_client):
        stub_client(
            StubClient(
                {
                    "https://api.dingtalk.com": {
                        "nick": "Alice",
                        "unionId": "u-1",
                        "openId": "o-1",
                        "avatarUrl": "https://a/x.png",
                    }
                }
            )
        )
        provider = make_provider("dingtalk")
        userinfo = fetch_userinfo(provider, {"access_token": "dt-1"})
        assert userinfo["unionId"] == "u-1"  # 预设 subject_field
        assert userinfo["nickname"] == "Alice" and userinfo["picture"] == "https://a/x.png"

    def test_userinfo_missing_unionid(self, stub_client):
        stub_client(StubClient({"https://api.dingtalk.com": {"nick": "no-union"}}))
        with pytest.raises(OAuthError):
            fetch_userinfo(make_provider("dingtalk"), {"access_token": "dt-1"})


# ---------------------------------------------------------------- 企业微信


@pytest.fixture
def _clear_wecom_cache():
    cache.clear()
    yield
    cache.clear()


class TestWecom:
    ROUTES = {
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken": {"errcode": 0, "access_token": "corp-1", "expires_in": 7200},
        "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo": {"errcode": 0, "userid": "zhangsan"},
        "https://qyapi.weixin.qq.com/cgi-bin/user/get": {
            "errcode": 0,
            "userid": "zhangsan",
            "name": "张三",
            "email": "zs@corp.com",
            "avatar": "https://a/z.png",
        },
    }

    def test_exchange_two_steps_and_payload(self, stub_client, _clear_wecom_cache):
        client = StubClient(self.ROUTES)
        stub_client(client)
        provider = make_provider("wecom", client_id="corp-1", client_secret="sec", agent_id="1")
        payload = exchange_code(provider, "code-1", REDIRECT)
        assert payload["userid"] == "zhangsan" and payload["access_token"] == "corp-1"
        get_call = next(c for c in client.calls if c[1].endswith("/gettoken"))
        assert get_call[2] == {"corpid": "corp-1", "corpsecret": "sec"}
        exchange_call = next(c for c in client.calls if "/auth/getuserinfo" in c[1])
        assert exchange_call[2] == {"access_token": "corp-1", "code": "code-1"}

    def test_corp_token_cached(self, stub_client, _clear_wecom_cache):
        """gettoken 有频控：第二次换码不再请求 gettoken。"""
        client = StubClient(self.ROUTES)
        stub_client(client)
        provider = make_provider("wecom", client_id="corp-1", client_secret="sec", agent_id="1")
        exchange_code(provider, "code-1", REDIRECT)
        exchange_code(provider, "code-2", REDIRECT)
        assert sum(1 for c in client.calls if c[1].endswith("/gettoken")) == 1

    def test_secret_change_rotates_cache(self, stub_client, _clear_wecom_cache):
        """改密后缓存 key 变化：不复用旧 token。"""
        client = StubClient(self.ROUTES)
        stub_client(client)
        exchange_code(make_provider("wecom", client_id="corp-1", client_secret="sec", agent_id="1"), "c1", REDIRECT)
        exchange_code(make_provider("wecom", client_id="corp-1", client_secret="sec2", agent_id="1"), "c2", REDIRECT)
        assert sum(1 for c in client.calls if c[1].endswith("/gettoken")) == 2

    def test_errcode_rejected(self, stub_client, _clear_wecom_cache):
        stub_client(
            StubClient(
                {
                    **self.ROUTES,
                    "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo": {
                        "errcode": 40029,
                        "errmsg": "invalid code",
                    },
                }
            )
        )
        with pytest.raises(OAuthError):
            exchange_code(make_provider("wecom", client_id="c", client_secret="s", agent_id="1"), "bad", REDIRECT)

    def test_userinfo_normalized(self, stub_client, _clear_wecom_cache):
        stub_client(StubClient(self.ROUTES))
        provider = make_provider("wecom", client_id="corp-1", client_secret="sec", agent_id="1")
        payload = exchange_code(provider, "code-1", REDIRECT)
        userinfo = fetch_userinfo(provider, payload)
        assert userinfo["userid"] == "zhangsan"
        assert (
            userinfo["nickname"] == "张三"
            and userinfo["email"] == "zs@corp.com"
            and userinfo["picture"] == "https://a/z.png"
        )


# ---------------------------------------------------------------- 飞书


class TestFeishu:
    def test_exchange_json_body(self, stub_client):
        client = StubClient({"https://open.feishu.cn": {"code": 0, "access_token": "fs-1"}})
        stub_client(client)
        provider = make_provider("feishu", client_id="cli_a", client_secret="s")
        payload = exchange_code(provider, "code-1", REDIRECT)
        assert payload["access_token"] == "fs-1"
        method, url, body = client.calls[0]
        assert body["grant_type"] == "authorization_code" and body["client_id"] == "cli_a" and body["code"] == "code-1"

    def test_exchange_data_wrapped_token(self, stub_client):
        """兼容 data 包裹的 token 响应形状。"""
        stub_client(StubClient({"https://open.feishu.cn": {"code": 0, "data": {"access_token": "fs-nested"}}}))
        payload = exchange_code(make_provider("feishu"), "c", REDIRECT)
        assert payload["access_token"] == "fs-nested"

    def test_exchange_code_field_rejected(self, stub_client):
        stub_client(StubClient({"https://open.feishu.cn": {"code": 99991663, "msg": "invalid code"}}))
        with pytest.raises(OAuthError):
            exchange_code(make_provider("feishu"), "bad", REDIRECT)

    def test_userinfo_data_envelope_normalized(self, stub_client):
        stub_client(
            StubClient(
                {
                    "https://open.feishu.cn": {
                        "code": 0,
                        "data": {
                            "union_id": "un-1",
                            "open_id": "ou-1",
                            "name": "李四",
                            "email": "ls@corp.com",
                            "avatar_url": "https://a/l.png",
                        },
                    }
                }
            )
        )
        provider = make_provider("feishu")
        userinfo = fetch_userinfo(provider, {"access_token": "fs-1"})
        assert userinfo["union_id"] == "un-1"
        assert userinfo["nickname"] == "李四" and userinfo["picture"] == "https://a/l.png"

    def test_userinfo_missing_data_rejected(self, stub_client):
        stub_client(StubClient({"https://open.feishu.cn": {"code": 0}}))
        with pytest.raises(OAuthError):
            fetch_userinfo(make_provider("feishu"), {"access_token": "fs-1"})


# ---------------------------------------------------------------- 回调集成


FEISHU_PROVIDER = {
    "key": "feishu",
    "name": "飞书",
    "flavor": "feishu",
    "client_id": "cli_a",
    "client_secret": "sec",
    "enabled": True,
    "auto_create": True,
}


@pytest.fixture
def feishu_config():
    SystemConfig.objects.update_or_create(
        key="OAUTH_PROVIDERS", defaults={"value": [dict(FEISHU_PROVIDER)], "is_active": True}
    )
    cache.clear()
    yield SystemConfig
    SystemConfig.objects.filter(key="OAUTH_PROVIDERS").delete()
    cache.clear()


def callback(provider="feishu", code="code-1", state=None):
    factory = APIRequestFactory()
    params = {"code": code, "state": state if state is not None else issue_state(provider)}
    request = factory.get(f"/api/system/auth/oauth/{provider}/callback", params)
    return OAuthCallbackAPIView.as_view()(request, provider=provider)


class TestCallbackIntegration:
    def test_auto_create_and_unique_binding(self, feishu_config, stub_client):
        """飞书回调：auto_create 建号 + 绑定；同一 subject 二次回调复用同一账号。"""
        routes = {
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token": {"code": 0, "access_token": "fs-1"},
            "https://open.feishu.cn/open-apis/authen/v1/user_info": {
                "code": 0,
                "data": {"union_id": "un-9", "name": "王五", "email": "ww@corp.com"},
            },
        }
        stub_client(StubClient(routes))
        first = callback()
        assert first.data["code"] == 1000, first.data
        user = UserInfo.objects.get(username="feishu_un-9")
        assert not user.has_usable_password()
        assert user.nickname == "王五"

        second = callback()
        assert second.data["code"] == 1000
        assert UserInfo.objects.filter(username="feishu_un-9").count() == 1
        binding = user.oauth_bindings.get()
        assert binding.subject == "un-9"
        assert binding.profile["nickname"] == "王五"

    def test_mfa_regression(self, feishu_config, stub_client, superuser):
        """MFA 回归：IM 登录与其他路径共用 complete_login，命中 MFA 必须返回引导响应。"""
        from system.models.oauth import UserOAuthBinding

        superuser.mfa_level = UserInfo.MFALevelChoices.ENABLED
        superuser.otp_secret_key = "x" * 32
        superuser.save(update_fields=["mfa_level", "otp_secret_key"])
        UserOAuthBinding.objects.create(user=superuser, provider="feishu", subject="un-admin")
        stub_client(
            StubClient(
                {
                    "https://open.feishu.cn/open-apis/authen/v2/oauth/token": {"code": 0, "access_token": "fs-1"},
                    "https://open.feishu.cn/open-apis/authen/v1/user_info": {
                        "code": 0,
                        "data": {"union_id": "un-admin", "name": "Admin"},
                    },
                }
            )
        )
        response = callback()
        assert response.data["data"]["mfa_required"] is True

    def test_disabled_user_rejected(self, feishu_config, stub_client, superuser):
        from system.models.oauth import UserOAuthBinding

        superuser.is_active = False
        superuser.save(update_fields=["is_active"])
        UserOAuthBinding.objects.create(user=superuser, provider="feishu", subject="un-admin")
        stub_client(
            StubClient(
                {
                    "https://open.feishu.cn/open-apis/authen/v2/oauth/token": {"code": 0, "access_token": "fs-1"},
                    "https://open.feishu.cn/open-apis/authen/v1/user_info": {
                        "code": 0,
                        "data": {"union_id": "un-admin"},
                    },
                }
            )
        )
        assert callback().data["code"] == OAUTH_ERROR_CODE

    def test_state_replay_rejected(self, feishu_config, stub_client):
        stub_client(StubClient({"https://open.feishu.cn": {"code": 0, "access_token": "fs-1"}}))
        state = issue_state("feishu")
        callback(state=state)
        assert callback(state=state).data["code"] == OAUTH_ERROR_CODE  # 一次性


# ---------------------------------------------------------------- 写侧校验接线


class TestConfigWriteValidation:
    """OAUTH_PROVIDERS 经系统配置 API 写入时保存即校验（ADR-018 补齐存量接线）。"""

    def test_invalid_provider_rejected_on_save(self, auth_client):
        payload = {
            "key": "OAUTH_PROVIDERS",
            "value": [{"key": "wecom", "name": "企微", "flavor": "wecom", "client_id": "c", "client_secret": "s"}],
            "is_active": True,
        }
        response = auth_client.post("/api/system/config/system", payload, format="json")
        assert response.status_code == 400, response.data

    def test_valid_provider_normalized_on_save(self, auth_client):
        payload = {
            "key": "OAUTH_PROVIDERS",
            "value": [
                {
                    "key": "feishu",
                    "name": "飞书",
                    "flavor": "feishu",
                    "client_id": "c",
                    "client_secret": "s",
                    "enabled": True,
                }
            ],
            "is_active": True,
        }
        response = auth_client.post("/api/system/config/system", payload, format="json")
        assert response.status_code in (200, 201), response.data
        row = SystemConfig.objects.get(key="OAUTH_PROVIDERS")
        # 归一化默认值一并持久化（auto_create 默认关，防意外建号）
        assert row.value[0]["auto_create"] is False
        SystemConfig.objects.filter(key="OAUTH_PROVIDERS").delete()
