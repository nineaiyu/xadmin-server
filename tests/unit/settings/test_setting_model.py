# -*- coding: utf-8 -*-
"""settings app 模型与序列化器单元测试（P2.8 盲区收口）。

settings app 此前不在 .coveragerc source 内，从未被覆盖率测量；
本文件覆盖 Setting 模型加解密 / 刷新 / 文件存取与 BasicSettingSerializer 钩子。
"""

import io
import json

import pytest
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import InMemoryUploadedFile

from settings.models import Setting

pytestmark = pytest.mark.django_db


class TestSettingModel:
    def test_str_returns_name(self):
        assert str(Setting(name="X")) == "X"

    def test_cleaned_value_roundtrip(self):
        assert Setting(name="PLAIN", value=json.dumps({"a": 1})).cleaned_value == {"a": 1}

    def test_cleaned_value_empty_is_none(self):
        assert Setting(name="EMPTY", value="").cleaned_value is None

    def test_cleaned_value_corrupt_json_falls_back_to_none(self):
        assert Setting(name="BROKEN", value="{not-json").cleaned_value is None

    def test_cleaned_value_encrypted_roundtrip(self, db):
        secret = Setting(name="SECRET", encrypted=True)
        secret.cleaned_value = {"token": "abc"}
        secret.save()
        # 落库为密文，读取时解密还原
        assert secret.value != json.dumps({"token": "abc"})
        assert secret.cleaned_value == {"token": "abc"}

    def test_cleaned_value_corrupt_cipher_falls_back_to_none(self):
        assert Setting(name="BROKEN_SECRET", encrypted=True, value="not-cipher").cleaned_value is None

    def test_cleaned_value_setter_converts_set(self):
        setting = Setting(name="SETVAL")
        setting.cleaned_value = {"b", "a"}
        assert sorted(setting.cleaned_value) == ["a", "b"]

    def test_cleaned_value_setter_json_error_wrapped(self, monkeypatch):
        import settings.models as models

        def boom(obj):
            raise json.JSONDecodeError("err", "doc", 0)

        monkeypatch.setattr(models.json, "dumps", boom)
        with pytest.raises(ValueError):
            Setting(name="BAD").cleaned_value = {"a": 1}

    def test_refresh_setting_and_refresh_item(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "REFRESH_TARGET", None, raising=False)
        monkeypatch.setattr(settings, "REFRESH_ITEM_KEY", None, raising=False)

        row = Setting.objects.create(name="REFRESH_TARGET", value="123")
        row.refresh_setting()
        assert settings.REFRESH_TARGET == 123

        Setting.refresh_item(("REFRESH_ITEM_KEY", "v"))
        assert settings.REFRESH_ITEM_KEY == "v"

    def test_refresh_all_settings_swallows_errors(self, db, monkeypatch):
        Setting.objects.create(name="REFRESH_ALL", value="1")

        def boom(self):
            raise RuntimeError("boom")

        monkeypatch.setattr(Setting, "refresh_setting", boom)
        Setting.refresh_all_settings()

    def test_save_to_file_and_update_or_create_with_upload(self, monkeypatch):
        storage = InMemoryStorage()
        monkeypatch.setattr("settings.models.default_storage", storage)

        def make_upload(name="logo.png", content=b"logo-bytes"):
            return InMemoryUploadedFile(io.BytesIO(content), None, name, "image/png", len(content), None)

        url = Setting.save_to_file(make_upload())
        assert "logo.png" in url
        assert storage.open("upload/settings/logo.png").read() == b"logo-bytes"

        changed, setting = Setting.update_or_create(name="LOGO_FILE", value=make_upload(), category="basic")
        assert changed is True
        assert setting.cleaned_value.startswith("/media/upload/settings/logo")

        # 相同内容重复保存不视为变更（幂等）
        changed_again, _ = Setting.update_or_create(name="LOGO_FILE", value=setting.cleaned_value)
        assert changed_again is False


class TestBasicSettingSerializerHooks:
    def test_validate_site_url_defaults_and_strips_trailing_slash(self):
        from settings.serializers.basic import BasicSettingSerializer

        serializer = BasicSettingSerializer()
        assert serializer.validate_SITE_URL("") == "http://127.0.0.1"
        assert serializer.validate_SITE_URL("http://x.local/") == "http://x.local"

    def test_post_save_notifies_only_on_permission_field_change(self):
        from settings.serializers.basic import BasicSettingSerializer, invalid_user_cache_signal

        received = []

        def receiver(sender, **kwargs):
            received.append(kwargs)

        invalid_user_cache_signal.connect(receiver, weak=False)
        try:
            serializer = BasicSettingSerializer()
            serializer._change_fields = ["PERMISSION_FIELD_ENABLED"]
            serializer.post_save()
            assert received and received[0]["user_pk"] == "*"

            received.clear()
            serializer._change_fields = ["EMAIL_ENABLED"]
            serializer.post_save()
            assert received == []
        finally:
            invalid_user_cache_signal.disconnect(receiver)
