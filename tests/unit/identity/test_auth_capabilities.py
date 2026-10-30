# -*- coding: utf-8 -*-
"""登录能力清单(auth/capabilities)的组合语义守护。

口径:入口可见性 = 场景开关 AND 投递通道可用(EMAIL_ENABLED / SMS_ENABLED);
注册/重置因流程必须经短信或邮箱验证码,通道全关时入口整体隐藏。
oauth 部分只做「已启用 provider 的 key/name/flavor 投影」。
"""

import pytest

from identity.services.auth_capabilities import build_login_capabilities

pytestmark = pytest.mark.django_db


@pytest.fixture
def no_oauth(monkeypatch):
    """oauth provider 置空(默认即休眠),排除数据库配置干扰。"""
    monkeypatch.setattr("identity.utils.oauth._config_providers", lambda: [])


@pytest.fixture
def all_channels(settings):
    """邮箱/短信投递通道与场景开关全开(运行时默认关闭,这里显式打开)。

    「可用」按硬判定:除开关外还需具备可实际投递的前提(测试邮件后端视为就绪,
    短信需服务商凭据完整)。
    """
    settings.EMAIL_ENABLED = True
    settings.SMS_ENABLED = True
    settings.SMS_BACKEND = "alibaba"
    settings.ALIBABA_ACCESS_KEY_ID = "test-key"
    settings.ALIBABA_ACCESS_KEY_SECRET = "test-secret"
    settings.ALIBABA_VERIFY_SIGN_NAME = "test-sign"
    settings.ALIBABA_VERIFY_TEMPLATE_CODE = "test-template"
    settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
    settings.SECURITY_LOGIN_BY_SMS_ENABLED = True


class TestBuildLoginCapabilities:
    def test_defaults_all_disabled_without_channels(self, settings, no_oauth):
        """通道全关(运行时默认):验证码/注册/重置入口全部隐藏。"""
        cap = build_login_capabilities()
        assert cap["access"] is True
        assert cap["basic"]["enabled"] is True
        assert cap["code"] == {"enabled": False, "email": False, "sms": False}
        assert cap["register"]["enabled"] is False
        assert cap["reset"]["enabled"] is False
        assert cap["oauth"] == []

    def test_all_enabled_with_channels(self, settings, all_channels, no_oauth):
        """通道全开:全部入口可见。"""
        cap = build_login_capabilities()
        assert cap["code"] == {"enabled": True, "email": True, "sms": True}
        assert cap["register"]["enabled"] is True
        assert cap["reset"]["enabled"] is True

    def test_login_access_off_masks_code_entry(self, settings, all_channels, no_oauth):
        """登录总开关只表达在顶层 access,不改写各方式自身可用性。"""
        settings.SECURITY_LOGIN_ACCESS_ENABLED = False
        cap = build_login_capabilities()
        assert cap["access"] is False
        assert cap["code"]["enabled"] is True

    def test_basic_off(self, settings, no_oauth):
        settings.SECURITY_LOGIN_BY_BASIC_ENABLED = False
        cap = build_login_capabilities()
        assert cap["basic"]["enabled"] is False

    def test_register_switch_off(self, settings, all_channels, no_oauth):
        """注册开关关闭:即使通道可用,入口仍隐藏。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = False
        cap = build_login_capabilities()
        assert cap["register"]["enabled"] is False
        assert cap["register"]["email"] is True

    def test_register_channels_all_off(self, settings, no_oauth):
        """注册开关开启但投递通道全关:流程走不通,入口隐藏。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = True
        cap = build_login_capabilities()
        assert cap["register"]["enabled"] is False

    def test_sms_channel_requires_backend(self, settings, no_oauth):
        """场景开短信但 SMS_ENABLED 关:短信通道不可用。"""
        settings.SECURITY_LOGIN_BY_SMS_ENABLED = True
        settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        settings.SMS_ENABLED = False
        cap = build_login_capabilities()
        assert cap["code"] == {"enabled": True, "email": True, "sms": False}

    def test_sms_channel_requires_provider_credentials(self, settings, no_oauth):
        """SMS_ENABLED 开着但服务商凭据不全:短信通道不可用(硬判定与设置页同口径)。"""
        settings.SECURITY_LOGIN_BY_SMS_ENABLED = True
        settings.SMS_ENABLED = True
        settings.SMS_BACKEND = "alibaba"
        settings.ALIBABA_ACCESS_KEY_ID = ""
        settings.ALIBABA_ACCESS_KEY_SECRET = ""
        cap = build_login_capabilities()
        assert cap["code"]["sms"] is False

    def test_email_channel_requires_smtp_host(self, settings, no_oauth):
        """EMAIL_ENABLED 开着但 SMTP 主机为空:邮箱通道不可用。"""
        settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
        settings.EMAIL_HOST = ""
        cap = build_login_capabilities()
        assert cap["code"]["email"] is False

    def test_oauth_projection_only_public_fields(self, settings, no_oauth, monkeypatch):
        """oauth 只投影 key/name/flavor,未启用或不完整的 provider 不下发。"""
        monkeypatch.setattr(
            "identity.utils.oauth._config_providers",
            lambda: [
                {
                    "key": "github",
                    "name": "GitHub",
                    "client_id": "id-1",
                    "client_secret": "secret-1",
                    "enabled": True,
                    "flavor": "github",
                },
                {"key": "draft", "name": "Draft", "enabled": True},  # 缺 client_id
                {
                    "key": "nosecret",
                    "name": "NoSecret",
                    "client_id": "id-2",
                    "enabled": True,
                },  # 缺 client_secret:授权必然失败,不再下发(登录页不显示死按钮)
                {"key": "off", "name": "Off", "client_id": "id-3", "enabled": False},
            ],
        )
        cap = build_login_capabilities()
        assert cap["oauth"] == [{"key": "github", "name": "GitHub", "flavor": "github"}]
