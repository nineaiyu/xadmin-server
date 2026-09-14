# -*- coding: utf-8 -*-
"""LDAP 设置 API 集成测试：鉴权/越权 / 凭据加密不回显 / 连接测试降级。"""

import pytest
from django.conf import settings as dj_settings

from settings.models import Setting
from system.models import LdapUserBinding, UserLoginLog
from system.serializers.user import ResetPasswordSerializer  # noqa: PLC2701
from system.views.auth.login import _login_type_for  # noqa: PLC2701

pytestmark = pytest.mark.django_db

LDAP_BASE = "/api/settings/ldap"

PAYLOAD = {
    "LDAP_AUTH_ENABLED": True,
    "LDAP_AUTH_PRIORITY": "local_first",
    "LDAP_AUTH_AUTO_CREATE": True,
    "LDAP_SERVER_URI": "ldap://directory.corp.com",
    "LDAP_START_TLS": False,
    "LDAP_BIND_DN": "cn=svc,dc=corp,dc=com",
    "LDAP_BIND_PASSWORD": "S3cret-Bind-Pwd",
    "LDAP_CONNECT_TIMEOUT": 10,
    "LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
    "LDAP_USER_FILTER": "(objectClass=person)",
    "LDAP_ATTR_USERNAME": "sAMAccountName",
    "LDAP_ATTR_NICKNAME": "cn",
    "LDAP_ATTR_EMAIL": "mail",
    "LDAP_ATTR_PHONE": "telephoneNumber",
    "LDAP_DEPT_ENABLED": True,
    "LDAP_DEPT_SEARCH_BASE": "ou=depts,dc=corp,dc=com",
    "LDAP_SYNC_ENABLED": True,
    "LDAP_SYNC_AUTO_CREATE": True,
    "LDAP_SYNC_MISSING_POLICY": "deactivate",
}


class TestAuthz:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(LDAP_BASE).status_code == 401

    def test_normal_user_without_permission_rejected(self, api_client, normal_user):
        """越权：普通用户无 LDAP 设置权限点 → 403。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(LDAP_BASE).status_code == 403
        assert api_client.patch(LDAP_BASE, PAYLOAD, format="json").status_code == 403
        assert api_client.post(LDAP_BASE, PAYLOAD, format="json").status_code == 403

    def test_superuser_allowed(self, auth_client):
        response = auth_client.get(LDAP_BASE)
        assert response.status_code == 200


class TestRetrieveAndSave:
    def test_retrieve_masks_password(self, auth_client):
        """BIND_PASSWORD 永不回传，其余配置正常回显。"""
        response = auth_client.get(LDAP_BASE).json()
        data = response["data"]
        assert "LDAP_BIND_PASSWORD" not in data
        assert data["LDAP_SERVER_URI"] == ""
        assert data["LDAP_AUTH_ENABLED"] is False  # 默认关闭

    def test_partial_update_persists_and_encrypts(self, auth_client):
        response = auth_client.patch(LDAP_BASE, PAYLOAD, format="json")
        assert response.status_code == 200, response.data
        # 密文落库：write_only 字段自动 encrypted=True
        row = Setting.objects.get(name="LDAP_BIND_PASSWORD")
        assert row.encrypted is True
        assert "S3cret" not in (row.value or "")
        # 其余字段明文
        assert Setting.objects.get(name="LDAP_SERVER_URI").encrypted is False
        # 持久化断言走 Setting 行（生产回写由 pubsub 完成，测试内按
        # test_monitor_settings.py 约定显式模拟 worker 侧回写）
        assert Setting.objects.get(name="LDAP_SERVER_URI").cleaned_value == "ldap://directory.corp.com"
        assert Setting.objects.get(name="LDAP_AUTH_ENABLED").cleaned_value is True
        Setting.objects.get(name="LDAP_SERVER_URI").refresh_setting()
        assert dj_settings.LDAP_SERVER_URI == "ldap://directory.corp.com"

    def test_blank_optional_fields_converge_to_defaults(self, auth_client):
        payload = dict(PAYLOAD, LDAP_USER_FILTER="", LDAP_ATTR_USERNAME="")
        auth_client.patch(LDAP_BASE, payload, format="json")
        assert Setting.objects.get(name="LDAP_USER_FILTER").cleaned_value == "(objectClass=person)"
        assert Setting.objects.get(name="LDAP_ATTR_USERNAME").cleaned_value == "sAMAccountName"


class TestConnectionTest:
    def test_missing_uri_rejected(self, auth_client):
        response = auth_client.post(LDAP_BASE, {"LDAP_SERVER_URI": ""}, format="json")
        assert response.json()["code"] == 1001

    def test_unreachable_directory_readable_error(self, auth_client):
        """目录不可达：可读失败提示（1002），不回显 Traceback。"""
        from ldap3.core.exceptions import LDAPException

        payload = dict(PAYLOAD, LDAP_USER_SEARCH_BASE="dc=corp,dc=com")
        from unittest import mock

        with mock.patch("system.ldap.sync.test_ldap_connection", side_effect=LDAPException("conn refused")):
            response = auth_client.post(LDAP_BASE, payload, format="json")
        body = response.json()
        assert body["code"] == 1002
        assert "Traceback" not in body["detail"]

    def test_success_returns_counts(self, auth_client):
        from unittest import mock

        with mock.patch(
            "system.ldap.sync.test_ldap_connection",
            return_value={"user_count": 12, "dept_count": 3},
        ):
            response = auth_client.post(LDAP_BASE, PAYLOAD, format="json")
        body = response.json()
        assert body["code"] == 1000
        assert body["data"] == {"user_count": 12, "dept_count": 3}


class TestLoginFlow:
    def test_login_type_marker(self, normal_user):
        assert _login_type_for(normal_user) == UserLoginLog.LoginTypeChoices.USERNAME
        normal_user._ldap_authenticated = True
        assert _login_type_for(normal_user) == UserLoginLog.LoginTypeChoices.LDAP

    def test_local_login_unaffected_when_directory_down(self, settings, superuser, monkeypatch):
        """核心降级保证：LDAP 开启但目录不可达时，本地账密照常认证成功。"""
        settings.LDAP_AUTH_ENABLED = True
        settings.LDAP_AUTH_PRIORITY = "ldap_first"  # 最激进配置下降级依旧

        from ldap3.core.exceptions import LDAPException

        def raise_down():
            raise LDAPException("connection refused")

        monkeypatch.setattr("system.ldap.auth.service_connection", raise_down)
        from django.contrib.auth import authenticate

        user = authenticate(username="admin", password="Admin@123456")
        assert user is not None
        assert user.pk == superuser.pk

    def test_local_login_still_works_when_ldap_user_wrong_password(self, settings, superuser, monkeypatch):
        """local_first：本地管理员永远用本地密码（目录密码不遮蔽）。"""
        settings.LDAP_AUTH_ENABLED = True
        from django.contrib.auth import authenticate

        user = authenticate(username="admin", password="Admin@123456")
        assert user is not None
        assert user.pk == superuser.pk

    def test_password_change_rejected_for_ldap_user(self, normal_user):
        LdapUserBinding.objects.create(user=normal_user, dn="cn=zhangsan,dc=corp,dc=com")
        serializer = ResetPasswordSerializer(instance=normal_user)
        with pytest.raises(Exception) as exc_info:
            serializer.update(normal_user, {"password": "whatever"})
        assert "LDAP" in str(exc_info.value)


class TestPeriodicTaskRegistered:
    def test_task_in_registry(self):
        """周期任务经 system.tasks 锚点被 autodiscover 发现（启动时 upsert 到 beat）。"""
        import system.tasks  # noqa: F401  触发子包任务注册
        from common.celery.decorator import get_register_period_tasks

        names = [next(iter(item)) for item in get_register_period_tasks()]
        assert "system.ldap.tasks.sync_ldap_directory_job" in names
