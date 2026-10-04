# -*- coding: utf-8 -*-
"""安全设置"绑定手机 / 绑定邮箱"配置读写互不干扰回归测试。

历史缺陷（2026-10 P0）：SecurityBindPhoneAuthSerializer 字段误用
SECURITY_BIND_EMAIL_* 前缀，"绑定手机"页签实际读写邮箱配置（Setting 按
name 唯一 upsert），且运行时真正消费的 SECURITY_BIND_PHONE_*
（system/views/auth/verify_code.py）在 UI 上不可配。
"""

import pytest

from settings.models import Setting

pytestmark = pytest.mark.django_db

# settings 路由为 SimpleRouter(False)（trailing_slash=False）：URL 一律不带尾斜杠
PHONE_URL = "/api/settings/bind/phone"
EMAIL_URL = "/api/settings/bind/email"

PHONE_KEY = "SECURITY_BIND_PHONE_ACCESS_ENABLED"
EMAIL_KEY = "SECURITY_BIND_EMAIL_ACCESS_ENABLED"


def _api_code(resp) -> int:
    return resp.data.get("code")


class TestBindSerializerFieldNames:
    def test_phone_serializer_uses_phone_keys_only(self):
        from settings.serializers.security import SecurityBindPhoneAuthSerializer

        field_names = set(SecurityBindPhoneAuthSerializer().get_fields())
        assert field_names, "serializer must declare fields"
        assert all(name.startswith("SECURITY_BIND_PHONE_") for name in field_names)

    def test_phone_and_email_serializer_fields_are_disjoint(self):
        from settings.serializers.security import (
            SecurityBindEmailAuthSerializer,
            SecurityBindPhoneAuthSerializer,
        )

        phone_fields = set(SecurityBindPhoneAuthSerializer().get_fields())
        email_fields = set(SecurityBindEmailAuthSerializer().get_fields())
        assert phone_fields.isdisjoint(email_fields)

    def test_runtime_consumed_phone_keys_are_all_configurable(self):
        """verify_code.py 运行时消费的 4 个 PHONE 键必须全部可在 UI 配置。"""
        from settings.serializers.security import SecurityBindPhoneAuthSerializer

        consumed = {
            "SECURITY_BIND_PHONE_ACCESS_ENABLED",
            "SECURITY_BIND_PHONE_CAPTCHA_ENABLED",
            "SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED",
            "SECURITY_BIND_PHONE_ENCRYPTED_ENABLED",
        }
        assert consumed <= set(SecurityBindPhoneAuthSerializer().get_fields())


class TestBindPhoneEmailIsolation:
    def test_phone_tab_writes_phone_keys_only(self, auth_client):
        resp = auth_client.patch(PHONE_URL, {PHONE_KEY: False}, format="json")
        assert resp.status_code == 200
        assert _api_code(resp) == 1000

        row = Setting.objects.filter(name=PHONE_KEY).first()
        assert row is not None
        assert row.cleaned_value is False
        assert not Setting.objects.filter(name=EMAIL_KEY).exists()

    def test_email_tab_writes_email_keys_only(self, auth_client):
        resp = auth_client.patch(EMAIL_URL, {EMAIL_KEY: False}, format="json")
        assert resp.status_code == 200
        assert _api_code(resp) == 1000

        row = Setting.objects.filter(name=EMAIL_KEY).first()
        assert row is not None
        assert row.cleaned_value is False
        assert not Setting.objects.filter(name=PHONE_KEY).exists()

    def test_phone_email_toggle_interleave_does_not_cross_contaminate(self, auth_client):
        """先关手机绑定再关邮箱绑定，两键互不覆盖（缺陷场景：关一个连带关另一个）。"""
        assert auth_client.patch(PHONE_URL, {PHONE_KEY: False}, format="json").status_code == 200
        assert auth_client.patch(EMAIL_URL, {EMAIL_KEY: False}, format="json").status_code == 200

        assert Setting.objects.get(name=PHONE_KEY).cleaned_value is False
        assert Setting.objects.get(name=EMAIL_KEY).cleaned_value is False

        # 反向再验证：重新开启手机绑定不影响邮箱配置
        assert auth_client.patch(PHONE_URL, {PHONE_KEY: True}, format="json").status_code == 200
        assert Setting.objects.get(name=PHONE_KEY).cleaned_value is True
        assert Setting.objects.get(name=EMAIL_KEY).cleaned_value is False

    def test_phone_tab_retrieve_returns_phone_keys(self, auth_client):
        resp = auth_client.get(PHONE_URL)
        assert resp.status_code == 200
        data = resp.data.get("data") or {}
        assert PHONE_KEY in data
        assert all(key.startswith("SECURITY_BIND_PHONE_") for key in data)

    def test_email_tab_retrieve_returns_email_keys(self, auth_client):
        resp = auth_client.get(EMAIL_URL)
        assert resp.status_code == 200
        data = resp.data.get("data") or {}
        assert EMAIL_KEY in data
        assert all(key.startswith("SECURITY_BIND_EMAIL_") for key in data)
