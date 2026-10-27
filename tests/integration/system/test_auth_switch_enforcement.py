# -*- coding: utf-8 -*-
"""登录方式开关的接口层强校验(capabilities 端点 + 提交侧拒绝)。

守护语义:开关关闭后不仅前端入口隐藏,对应提交接口同样拒绝——
账密登录(BY_BASIC)、重置密码(RESET_ACCESS)、验证码登录通道复核。
"""

import pytest

pytestmark = pytest.mark.django_db

CAPABILITIES_URL = "/api/identity/auth/capabilities"
BASIC_LOGIN_URL = "/api/identity/login/basic"
RESET_URL = "/api/identity/auth/reset"


@pytest.fixture
def login_free(settings):
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


class TestCapabilitiesEndpoint:
    def test_anonymous_accessible_with_shape(self, api_client, settings):
        """匿名可达;方式级字段齐全(oauth 默认休眠为空数组)。"""
        resp = api_client.get(CAPABILITIES_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert set(data) == {"access", "lifetime", "basic", "code", "register", "reset", "oauth"}
        assert set(data["basic"]) == {
            "enabled",
            "captcha",
            "captcha_types",
            "captcha_image_fallback",
            "captcha_flow",
            "token",
            "encrypted",
        }
        for way in ("code", "register", "reset"):
            assert set(data[way]) == {"enabled", "email", "sms"}

    def test_reflects_disabled_register(self, api_client, settings):
        settings.SECURITY_REGISTER_ACCESS_ENABLED = False
        resp = api_client.get(CAPABILITIES_URL)
        assert resp.data["data"]["register"]["enabled"] is False


class TestBasicSwitchEnforced:
    def test_basic_login_rejected_when_disabled(self, api_client, normal_user, login_free, settings):
        """BY_BASIC 关闭:凭证正确也在接口层被拒(不能只拦前端入口)。"""
        settings.SECURITY_LOGIN_BY_BASIC_ENABLED = False
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        assert "Login forbidden" in str(resp.data["detail"]) or "禁止登录" in str(resp.data["detail"])

    def test_basic_login_still_works_when_enabled(self, api_client, normal_user, login_free):
        """开关开启:正常登录(强校验不误伤既有链路)。"""
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data


class TestResetSwitchEnforced:
    def test_reset_rejected_when_disabled(self, api_client, settings):
        """RESET 开关关闭:无需有效验证码载荷,接口层先行拒绝。"""
        settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED = False
        resp = api_client.post(RESET_URL, {"verify_token": "t", "verify_code": "1", "password": "x"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001


class TestVerifyCodeLoginChannelEnforced:
    def test_channel_off_rejected_at_submit(self, api_client, normal_user, login_free, settings, monkeypatch):
        """发码后通道被关:提交侧复核拒绝(旧 verify_token 不能绕过)。"""
        settings.EMAIL_ENABLED = True
        settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
        monkeypatch.setattr(
            "identity.views.auth.login.verify_sms_email_code",
            lambda request, block: ("email", "zhangsan@example.com", "token"),
        )
        settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = False  # 发码后关闭
        resp = api_client.post(
            "/api/identity/login/code",
            {"verify_token": "t", "verify_code": "123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001

    def test_sms_channel_off_rejected_at_submit(self, api_client, normal_user, login_free, settings, monkeypatch):
        """短信通道同理:phone 提交在 LOGIN_BY_SMS 关闭后被拒。"""
        monkeypatch.setattr(
            "identity.views.auth.login.verify_sms_email_code",
            lambda request, block: ("phone", "13800000000", "token"),
        )
        resp = api_client.post(
            "/api/identity/login/code",
            {"verify_token": "t", "verify_code": "123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
