# -*- coding: utf-8 -*-
"""设置项「实际生效状态」诊断(build_setting_effective)的组合矩阵守护。

口径:场景 enabled 与登录页能力清单(identity.services.auth_capabilities)
同源(直接读取),reasons 解释未生效原因(仅未生效时给出),notices 为已生效
但存在配置风险的软提示(SMTP / 阿里云凭据);LDAP 配置缺失为硬阻断(与
LdapConfigError 一致)。

开关一律经 pytest-django settings fixture 修改(用例间自动还原,避免进程内
顺序泄漏);测试库种子可能带入非默认通道开关,不依赖环境默认值作断言前提。
"""

import pytest
from django.utils.translation import gettext as _

from identity.services.auth_capabilities import build_login_capabilities
from settings.services import build_setting_effective

pytestmark = pytest.mark.django_db


@pytest.fixture
def no_oauth(monkeypatch):
    monkeypatch.setattr("identity.utils.oauth._config_providers", lambda: [])


@pytest.fixture
def channels_off(settings):
    """投递服务与各场景通道开关全部关闭(显式基线,不依赖环境默认值)。"""
    settings.EMAIL_ENABLED = False
    settings.SMS_ENABLED = False
    for key in (
        "SECURITY_LOGIN_BY_EMAIL_ENABLED",
        "SECURITY_LOGIN_BY_SMS_ENABLED",
        "SECURITY_REGISTER_BY_EMAIL_ENABLED",
        "SECURITY_REGISTER_BY_SMS_ENABLED",
        "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED",
        "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED",
    ):
        setattr(settings, key, False)


class TestRegisterEffective:
    def test_register_switch_on_channels_off(self, settings, channels_off, no_oauth):
        """用户报告的核心场景:允许注册已开,但邮箱/短信服务未配置 → 未生效+原因。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = True
        payload = build_setting_effective("security_register_auth")
        (item,) = payload["items"]
        assert item["title"] == str(_("Register"))
        assert item["enabled"] is False
        assert item["reasons"], "必须给出未生效原因"
        assert item["reasons"][-1] == str(_("Register requires at least one available channel (email or sms)"))
        # 原因与登录页能力清单同口径:enabled 取自同一来源
        assert item["enabled"] == build_login_capabilities()["register"]["enabled"]

    def test_register_switch_off(self, settings, channels_off, no_oauth):
        settings.SECURITY_REGISTER_ACCESS_ENABLED = False
        item = build_setting_effective("security_register_auth")["items"][0]
        assert item["enabled"] is False
        assert str(_("Register switch is off")) in item["reasons"]

    def test_register_effective_with_email_channel(self, settings, channels_off, no_oauth):
        """邮箱场景开关 + 邮件服务均开 → 生效,reasons 为空(不列其他关闭通道)。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = True
        settings.SECURITY_REGISTER_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        item = build_setting_effective("security_register_auth")["items"][0]
        assert item["enabled"] is True
        assert item["reasons"] == []


class TestLoginEffective:
    def test_login_basic_on_code_off(self, settings, channels_off, no_oauth):
        settings.SECURITY_LOGIN_ACCESS_ENABLED = True
        settings.SECURITY_LOGIN_BY_BASIC_ENABLED = True
        items = build_setting_effective("security_login_auth")["items"]
        assert [item["enabled"] for item in items] == [True, False]
        assert items[1]["reasons"], "验证码登录(通道全关)必须解释原因"

    def test_login_code_effective_with_channel(self, settings, channels_off, no_oauth):
        settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        items = build_setting_effective("security_login_auth")["items"]
        assert items[1]["enabled"] is True
        assert items[1]["reasons"] == []


class TestBindEffective:
    def test_bind_email_blocked_by_service(self, settings, channels_off, no_oauth):
        settings.SECURITY_BIND_EMAIL_ACCESS_ENABLED = True
        item = build_setting_effective("security_bind_email_auth")["items"][0]
        assert item["enabled"] is False
        assert str(_("Email service is disabled")) in item["reasons"]

    def test_bind_phone_blocked_by_service(self, settings, channels_off, no_oauth):
        settings.SECURITY_BIND_PHONE_ACCESS_ENABLED = True
        item = build_setting_effective("security_bind_phone_auth")["items"][0]
        assert item["enabled"] is False


class TestDeliveryServiceAdvisory:
    def test_email_service_on_without_host_is_notice_not_blocker(self, settings, channels_off, no_oauth):
        """SMTP 缺失是软提示:服务开关开着就算生效,风险以 notices 表达。"""
        settings.EMAIL_ENABLED = True
        settings.EMAIL_HOST = ""
        item = build_setting_effective("email")["items"][0]
        assert item["enabled"] is True
        assert item["reasons"] == []
        assert item["notices"]
        assert "EMAIL_HOST" in item["notices"][0]

    def test_email_service_off(self, settings, channels_off, no_oauth):
        settings.EMAIL_ENABLED = False
        item = build_setting_effective("email")["items"][0]
        assert item["enabled"] is False
        assert item["notices"] == []

    def test_sms_service_on_without_alibaba_creds(self, settings, channels_off, no_oauth):
        settings.SMS_ENABLED = True
        settings.SMS_BACKEND = "alibaba"
        settings.ALIBABA_ACCESS_KEY_ID = ""
        item = build_setting_effective("sms")["items"][0]
        assert item["enabled"] is True
        assert item["notices"]


class TestLdapHardBlock:
    def test_ldap_on_without_server_uri(self, settings, no_oauth):
        """LDAP 开了但服务器地址未配置:硬阻断(登录侧静默回退本地,此处给出唯一可见信号)。"""
        settings.LDAP_AUTH_ENABLED = True
        settings.LDAP_SERVER_URI = ""
        item = build_setting_effective("ldap")["items"][0]
        assert item["enabled"] is False
        assert item["reasons"]
        assert item["notices"] == []

    def test_ldap_off(self, settings, no_oauth):
        settings.LDAP_AUTH_ENABLED = False
        item = build_setting_effective("ldap")["items"][0]
        assert item["enabled"] is False


class TestUnregisteredCategory:
    def test_unknown_category_returns_none(self, no_oauth):
        """未登记的 tab 不下发诊断(基类钩子默认空,不误伤其他设置端点)。"""
        assert build_setting_effective("security_password") is None
        assert build_setting_effective("nonexistent") is None
