# -*- coding: utf-8 -*-
"""「测试连接」表单值传参统一口径测试。

邮箱 / IM / LDAP 三个测试端点统一为：按表单值（未提交键回退已存配置）构造
只读快照传参，绝不 ``setattr(settings, ...)`` 改进程全局——并发期间真实请求
不可能读到测试值。此前邮箱只读已存配置、IM/LDAP 临时改全局再恢复。
"""

import pytest
from django.conf import settings as dj_settings
from django.core import mail
from django.utils.translation import gettext_lazy as _

from settings.utils.test_connection import build_test_values

pytestmark = pytest.mark.django_db

EMAIL_URL = "/api/settings/email"
IM_URL = "/api/settings/notify/im"
LDAP_URL = "/api/settings/ldap"


class TestBuildTestValues:
    def test_submitted_key_wins(self, settings):
        settings.EMAIL_HOST = "stored.smtp.local"
        values = build_test_values({"EMAIL_HOST": "form.smtp.local"}, {"EMAIL_HOST": "form.smtp.local"}, ["EMAIL_HOST"])
        assert values == {"EMAIL_HOST": "form.smtp.local"}

    def test_unsubmitted_key_falls_back_to_settings(self, settings):
        settings.EMAIL_HOST = "stored.smtp.local"
        values = build_test_values({}, {}, ["EMAIL_HOST"])
        assert values == {"EMAIL_HOST": "stored.smtp.local"}

    def test_secret_empty_falls_back_to_settings(self, settings):
        settings.EMAIL_HOST_PASSWORD = "stored-pwd"
        values = build_test_values(
            {"EMAIL_HOST_PASSWORD": ""}, {"EMAIL_HOST_PASSWORD": ""}, ["EMAIL_HOST_PASSWORD"], ["EMAIL_HOST_PASSWORD"]
        )
        assert values == {"EMAIL_HOST_PASSWORD": "stored-pwd"}

    def test_secret_nonempty_overrides(self, settings):
        settings.EMAIL_HOST_PASSWORD = "stored-pwd"
        values = build_test_values(
            {"EMAIL_HOST_PASSWORD": "new-pwd"},
            {"EMAIL_HOST_PASSWORD": "new-pwd"},
            ["EMAIL_HOST_PASSWORD"],
            ["EMAIL_HOST_PASSWORD"],
        )
        assert values == {"EMAIL_HOST_PASSWORD": "new-pwd"}


class TestEmailTestEndpoint:
    def test_connection_built_from_form_values(self, auth_client, settings, monkeypatch):
        """连接参数按表单值构造（密码留空沿用已存），不再只读已存配置。"""
        from settings.views import email as email_view

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        settings.EMAIL_HOST = "stored.smtp.local"
        settings.EMAIL_HOST_PASSWORD = "stored-pwd"
        settings.EMAIL_SUBJECT_PREFIX = "[xadmin] "

        captured = {}
        real_get_connection = email_view.get_connection

        def spy_get_connection(**kwargs):
            captured.update(kwargs)
            return real_get_connection(**kwargs)

        monkeypatch.setattr(email_view, "get_connection", spy_get_connection)
        mail.outbox = []

        resp = auth_client.post(
            EMAIL_URL,
            {
                "EMAIL_HOST": "form.smtp.local",
                "EMAIL_PORT": "465",
                "EMAIL_HOST_USER": "tester@test.local",
                "EMAIL_USE_SSL": True,
                "EMAIL_USE_TLS": False,
                "EMAIL_RECIPIENT": "to@test.local",
            },
        )

        assert resp.data["code"] == 1000, resp.data
        assert captured["host"] == "form.smtp.local", "表单值应优先于已存配置"
        assert captured["password"] == "stored-pwd", "密码留空应沿用已存值"
        assert captured["use_ssl"] is True and captured["use_tls"] is False
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "[xadmin] Test"

    def test_global_settings_untouched_after_test(self, auth_client, settings):
        """测试请求不得改写进程全局 settings。"""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        settings.EMAIL_HOST = "stored.smtp.local"
        mail.outbox = []

        resp = auth_client.post(
            EMAIL_URL,
            {
                "EMAIL_HOST": "form.smtp.local",
                "EMAIL_PORT": "465",
                "EMAIL_HOST_USER": "tester@test.local",
                "EMAIL_USE_SSL": True,
            },
        )

        assert resp.data["code"] == 1000, resp.data
        assert dj_settings.EMAIL_HOST == "stored.smtp.local"


class TestImTestEndpoint:
    def test_uses_form_values_fallback_stored(self, auth_client, settings, monkeypatch):
        """凭据按表单值构造（未提交键回退已存），不触碰进程全局。"""
        from integrations.sdk.im.dingtalk import DingTalkClient

        settings.DINGTALK_ENABLED = True
        settings.DINGTALK_APP_KEY = "stored-key"
        settings.DINGTALK_APP_SECRET = "stored-secret"
        settings.DINGTALK_AGENT_ID = "stored-agent"

        captured = {}

        def fake_token(self):
            captured.update(self.credentials)
            return "token"

        monkeypatch.setattr(DingTalkClient, "_cached_token", fake_token)

        resp = auth_client.post(
            IM_URL + "?channel=dingtalk",
            {"DINGTALK_APP_KEY": "form-key", "DINGTALK_AGENT_ID": "form-agent"},
        )

        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"] == {"DingTalk": str(_("OK"))}
        assert captured == {"app_key": "form-key", "app_secret": "stored-secret", "agent_id": "form-agent"}
        # 进程全局未被测试值污染（并发期间真实请求读不到测试值）
        assert dj_settings.DINGTALK_APP_KEY == "stored-key"
        assert dj_settings.DINGTALK_APP_SECRET == "stored-secret"
        assert dj_settings.DINGTALK_AGENT_ID == "stored-agent"


class TestLdapTestEndpoint:
    def test_config_snapshot_from_form_values(self, auth_client, settings, monkeypatch):
        """连接测试按表单值构造 LdapConfig 快照传参，不临时改写进程全局。"""
        import identity.ldap.sync as ldap_sync

        settings.LDAP_SERVER_URI = "ldap://stored"
        settings.LDAP_USER_SEARCH_BASE = "dc=stored"
        settings.LDAP_BIND_PASSWORD = "stored-pwd"

        captured = {}

        def fake_test(config):
            captured["config"] = config
            return {"user_count": 3, "dept_count": 1}

        monkeypatch.setattr(ldap_sync, "test_ldap_connection", fake_test)

        resp = auth_client.post(
            LDAP_URL,
            {"LDAP_SERVER_URI": "ldaps://form", "LDAP_USER_FILTER": "(cn=*)", "LDAP_BIND_PASSWORD": "form-pwd"},
        )

        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"] == {"user_count": 3, "dept_count": 1}
        cfg = captured["config"]
        assert cfg.server_uri == "ldaps://form", "表单值应优先于已存配置"
        assert cfg.user_search_base == "dc=stored", "未提交键应回退已存配置"
        assert cfg.user_filter == "(cn=*)"
        assert cfg.bind_password == "form-pwd"
        # 进程全局未被测试值污染
        assert dj_settings.LDAP_SERVER_URI == "ldap://stored"

    def test_empty_secret_falls_back_to_stored_password(self, auth_client, settings, monkeypatch):
        import identity.ldap.sync as ldap_sync

        settings.LDAP_SERVER_URI = "ldap://stored"
        settings.LDAP_USER_SEARCH_BASE = "dc=stored"
        settings.LDAP_BIND_PASSWORD = "stored-pwd"

        captured = {}
        monkeypatch.setattr(
            ldap_sync,
            "test_ldap_connection",
            lambda config: captured.update(config=config) or {"user_count": 0, "dept_count": 0},
        )

        resp = auth_client.post(LDAP_URL, {"LDAP_SERVER_URI": "ldap://stored", "LDAP_BIND_PASSWORD": ""})

        assert resp.data["code"] == 1000, resp.data
        assert captured["config"].bind_password == "stored-pwd"
