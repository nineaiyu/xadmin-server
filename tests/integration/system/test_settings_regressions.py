# -*- coding: utf-8 -*-
"""3.6 系统设置回归：IP 名单、设置序列化器、短信测试、登录策略目标与模板覆盖。"""

from datetime import time
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from identity.models import LoginAccessPolicy, UserRole
from identity.serializers.security import LoginAccessPolicySerializer
from notifications.models import MessageTemplate
from notifications.notifications import SYSTEM_MESSAGE_REGISTRY
from settings.serializers.basic import BasicSettingSerializer
from settings.serializers.email import EmailSettingSerializer
from settings.serializers.ldap import LdapSettingSerializer
from settings.serializers.setting import SettingSerializer
from settings.serializers.sms import AlibabaSMSSettingSerializer
from settings.utils.security import LoginIpBlockUtil
from settings.views.sms import SmsConfigViewSet

pytestmark = pytest.mark.django_db

BLOCK_URL = "/api/settings/ip/block"


class TestBlockIpList:
    def _block(self, ip: str) -> None:
        cache.set(LoginIpBlockUtil(ip).block_key, timezone.now().isoformat(), 600)

    def test_whitelisted_cidr_excluded_from_list(self, auth_client, settings):
        settings.SECURITY_LOGIN_IP_WHITE_LIST = ["192.168.0.0/24"]
        self._block("192.168.0.5")
        self._block("10.1.2.3")
        rows = auth_client.get(BLOCK_URL).json()["data"]["results"]
        ips = {row["ip"] for row in rows}
        assert "10.1.2.3" in ips
        assert "192.168.0.5" not in ips

    def test_non_numeric_pk_returns_404(self, auth_client):
        assert auth_client.get(f"{BLOCK_URL}/not-a-number").status_code in (400, 404)

    def test_pk_not_in_table_metadata(self, auth_client):
        columns = auth_client.get(f"{BLOCK_URL}/search-columns").json()["data"]
        pk = next(item for item in columns if item["key"] == "pk")
        assert pk.get("table_show") is None


class TestSettingSerializerGuards:
    def test_export_limit_rejects_zero(self):
        serializer = BasicSettingSerializer(data={"EXPORT_MAX_LIMIT": 0})
        assert not serializer.is_valid()
        assert BasicSettingSerializer(data={"EXPORT_MAX_LIMIT": 1}).is_valid()

    def test_blank_secrets_keep_existing_semantics(self):
        email = EmailSettingSerializer(
            data={"EMAIL_HOST": "smtp", "EMAIL_PORT": "25", "EMAIL_HOST_USER": "u", "EMAIL_HOST_PASSWORD": ""}
        )
        assert email.is_valid(), email.errors
        alibaba = AlibabaSMSSettingSerializer(
            data={
                "ALIBABA_ACCESS_KEY_ID": "ak",
                "ALIBABA_ACCESS_KEY_SECRET": "",
                "ALIBABA_VERIFY_SIGN_NAME": "sign",
                "ALIBABA_VERIFY_TEMPLATE_CODE": "tpl",
            }
        )
        assert alibaba.is_valid(), alibaba.errors

    def test_sensitive_key_forced_encrypted(self):
        serializer = SettingSerializer(
            data={"name": "EMAIL_HOST_PASSWORD", "value": "plain", "encrypted": False, "category": "email"}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["encrypted"] is True

    def test_ldap_partial_does_not_inject_defaults(self):
        serializer = LdapSettingSerializer(data={"LDAP_SYNC_ENABLED": True}, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert "LDAP_USER_FILTER" not in serializer.validated_data

    def test_ldap_group_map_rejects_unknown_role(self):
        serializer = LdapSettingSerializer(data={"LDAP_GROUP_ROLE_MAP": {"cn=ops": "missing-role"}})
        assert not serializer.is_valid()

    def test_ldap_group_map_accepts_existing_role(self):
        UserRole.objects.create(name="运维", code="ops")
        serializer = LdapSettingSerializer(data={"LDAP_GROUP_ROLE_MAP": {"cn=ops": "ops"}})
        assert serializer.is_valid(), serializer.errors


class TestSmsDefaults:
    def test_missing_category_defaults_to_alibaba(self):
        view = SmsConfigViewSet()
        view.request = Request(APIRequestFactory().post("/api/settings/sms/config", {}))
        assert view.get_serializer_class().__name__ == "AlibabaSMSSettingSerializer"

    def test_unknown_category_is_400(self):
        view = SmsConfigViewSet()
        view.request = Request(APIRequestFactory().post("/api/settings/sms/config/?category=nope", {}))
        with pytest.raises(ValidationError):
            view.get_serializer_class()

    def test_success_returns_business_code_1000(self, auth_client, monkeypatch):
        class _Client:
            def __init__(self, **_kwargs):
                pass

            def send_sms(self, **_kwargs):
                return None

        fake = SimpleNamespace(client=_Client)
        # 只替换 sms 视图模块里的 importlib 名字绑定：直接 patch
        # importlib.import_module 会改到全局 stdlib 模块，补丁窗口内任何
        # 「调用期 from importlib import import_module」的惰性导入（契约
        # PEP 562 解析等）都会拿到 fake，首次请求懒加载中间件即 ImportError
        monkeypatch.setattr("settings.views.sms.importlib", SimpleNamespace(import_module=lambda *_a, **_kw: fake))
        response = auth_client.post(
            "/api/settings/sms/config",
            {
                "SMS_TEST_PHONE": "13800000000",
                "ALIBABA_ACCESS_KEY_ID": "ak",
                "ALIBABA_VERIFY_SIGN_NAME": "sign",
                "ALIBABA_VERIFY_TEMPLATE_CODE": "tpl",
            },
            format="json",
        )
        assert response.json()["code"] == 1000, response.json()


class TestLoginPolicyTargetValidation:
    def _payload(self, target_type: str, target_value: str) -> dict:
        return {
            "name": f"策略-{target_type}",
            "priority": 10,
            "target_type": target_type,
            "target_value": target_value,
            "action": "reject",
        }

    def test_unknown_username_rejected(self):
        serializer = LoginAccessPolicySerializer(data=self._payload("user", "ghost-user"))
        assert not serializer.is_valid()

    def test_unknown_role_code_rejected(self):
        serializer = LoginAccessPolicySerializer(data=self._payload("role", "ghost-role"))
        assert not serializer.is_valid()

    def test_existing_user_and_role_accepted(self, superuser):
        UserRole.objects.create(name="值班", code="oncall")
        assert LoginAccessPolicySerializer(data=self._payload("user", superuser.username)).is_valid()
        assert LoginAccessPolicySerializer(data=self._payload("role", "oncall")).is_valid()

    def test_condition_summary_exposed(self, superuser):
        policy = LoginAccessPolicy.objects.create(
            name="工作时间",
            priority=10,
            target_type=LoginAccessPolicy.TargetType.ALL,
            weekdays=[1, 2, 3, 4, 5],
            start_time=time(9, 0),
            end_time=time(18, 0),
            action=LoginAccessPolicy.Action.REJECT,
            creator=superuser,
        )
        data = LoginAccessPolicySerializer(policy).data
        assert data["condition_summary"]
        assert "09:00" in data["condition_summary"]


class TestMessageTemplateOverride:
    def test_inactive_empty_override_still_reported(self, auth_client, superuser):
        message_type = SYSTEM_MESSAGE_REGISTRY[0]["message_type"]
        MessageTemplate.objects.create(
            message_type=message_type,
            subject_template="",
            body_template="",
            is_active=False,
            creator=superuser,
        )
        rows = auth_client.get("/api/notifications/message-templates").json()["data"]
        row = next(item for item in rows if item["message_type"] == message_type)
        assert row["has_override"] is True
        assert row["override"]["is_active"] is False
