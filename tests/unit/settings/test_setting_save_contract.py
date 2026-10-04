# -*- coding: utf-8 -*-
"""BaseSettingViewSet 保存链路显式契约测试。

锁定 perform_update 与 SettingSaveContractMixin 的组合行为：只存提交键、
write_only 密文留空不修改、响应载荷合并视图、change_fields 只含真变更、
post_save 联动只在实际变更时触发。
"""

import json

import pytest
from django.conf import settings as dj_settings

from settings.models import Setting
from system.services import invalid_user_cache_signal

pytestmark = pytest.mark.django_db

BASIC_URL = "/api/settings/basic"
LDAP_URL = "/api/settings/ldap"


class TestPersistOnlySubmittedKeys:
    def test_unsubmitted_default_field_not_persisted(self, auth_client):
        """带 default 的可选字段未提交时不落库（PUT 同口径：「改了什么存什么」）。"""
        resp = auth_client.patch(BASIC_URL, {"SITE_URL": "https://contract.example.com"})
        assert resp.data["code"] == 1000, resp.data

        names = set(Setting.objects.filter(category="basic").values_list("name", flat=True))
        assert "SITE_URL" in names
        # FRONT_END_WEB_WATERMARK_FONT_SIZE 声明了 default=16，validated_data 必含
        # 默认值——契约要求未提交键不得借此落库
        assert "FRONT_END_WEB_WATERMARK_FONT_SIZE" not in names

    def test_submitted_keys_persisted(self, auth_client):
        resp = auth_client.patch(BASIC_URL, {"SITE_URL": "https://contract.example.com"})
        row = Setting.objects.get(name="SITE_URL", category="basic")
        assert row.cleaned_value == "https://contract.example.com"
        assert resp.data["code"] == 1000


class TestResponsePayload:
    def test_response_merges_new_values_and_runtime_values(self, auth_client):
        """响应 = 未提交键取运行时当前值 + 变更键取新值。"""
        current_export_limit = dj_settings.EXPORT_MAX_LIMIT
        resp = auth_client.patch(BASIC_URL, {"SITE_URL": "https://merge.example.com"})
        data = resp.data["data"]
        assert data["SITE_URL"] == "https://merge.example.com"
        assert data["EXPORT_MAX_LIMIT"] == current_export_limit


class TestChangeFieldsContract:
    def test_post_save_fires_only_on_real_change(self, auth_client):
        """首次落库触发联动；值未变化不触发；再变化重新触发。"""
        received = []

        def receiver(sender, **kwargs):
            received.append(kwargs.get("user_pk"))

        invalid_user_cache_signal.connect(receiver, weak=False)
        try:
            target = not dj_settings.PERMISSION_DATA_ENABLED
            resp = auth_client.patch(BASIC_URL, {"PERMISSION_DATA_ENABLED": target})
            assert resp.data["code"] == 1000, resp.data
            assert received == ["*"], "首次落库（值与运行时不同）应触发 post_save 联动"

            received.clear()
            resp = auth_client.patch(BASIC_URL, {"PERMISSION_DATA_ENABLED": target})
            assert resp.data["code"] == 1000, resp.data
            assert received == [], "值未变化（changed=False）不得触发 post_save 联动"

            received.clear()
            resp = auth_client.patch(BASIC_URL, {"PERMISSION_DATA_ENABLED": not target})
            assert received == ["*"]
        finally:
            invalid_user_cache_signal.disconnect(receiver)


class TestWriteOnlySecretFallback:
    def test_empty_secret_keeps_stored_value(self, auth_client):
        """write_only 密文提交空值 = 不修改（回退已存值）。"""
        from common.base.utils import signer

        stored = "stored-ldap-password"
        Setting.objects.create(
            name="LDAP_BIND_PASSWORD",
            value=signer.encrypt(json.dumps(stored).encode("utf-8")).decode("utf-8"),
            encrypted=True,
            category="ldap",
        )

        resp = auth_client.patch(LDAP_URL, {"LDAP_BIND_PASSWORD": ""})
        assert resp.data["code"] == 1000, resp.data

        row = Setting.objects.get(name="LDAP_BIND_PASSWORD")
        assert row.cleaned_value == stored, "空密文提交不得清掉已存密码"


class TestContractMixinCompat:
    def test_deprecated_change_fields_alias_reads_and_warns(self):

        from settings.serializers.basic import BasicSettingSerializer

        serializer = BasicSettingSerializer()
        serializer.change_fields = ["PERMISSION_FIELD_ENABLED"]
        with pytest.warns(DeprecationWarning):
            assert serializer._change_fields == ["PERMISSION_FIELD_ENABLED"]
        with pytest.warns(DeprecationWarning):
            serializer._change_fields = ["EMAIL_ENABLED"]
        assert serializer.change_fields == ["EMAIL_ENABLED"]

    def test_post_save_default_noop(self):
        from settings.serializers.email import EmailSettingSerializer

        serializer = EmailSettingSerializer()
        serializer.change_fields = ["EMAIL_ENABLED"]
        serializer.post_save()  # 未覆写 post_save 的序列化器：契约默认无操作
