# -*- coding: utf-8 -*-
"""settings app 模型与序列化器单元测试（盲区收口）。

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

    def test_refresh_all_settings_skips_when_table_missing(self, db, monkeypatch, caplog):
        """裸库（未迁移）上整体跳过而非裸崩：django_ready 后台线程无兜底（§五 #21）。"""
        from django.db import connection

        monkeypatch.setattr(connection.introspection, "table_names", lambda *a, **kw: [])
        with caplog.at_level("WARNING"):
            Setting.refresh_all_settings()
        assert any("skip refresh_all_settings" in record.message for record in caplog.records)

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

    def test_repeated_same_name_keeps_single_row_and_return_semantics(self):
        """同名键重复保存不产生双行：(changed, instance) 返回语义与主键稳定性不变。"""
        changed, first = Setting.update_or_create(name="RACE_DUP", value={"a": 1}, category="basic")
        assert changed is True
        assert first.cleaned_value == {"a": 1}

        unchanged, second = Setting.update_or_create(name="RACE_DUP", value={"a": 1})
        assert unchanged is False

        changed_again, third = Setting.update_or_create(name="RACE_DUP", value={"b": 2})
        assert changed_again is True
        assert third.cleaned_value == {"b": 2}

        assert first.pk == second.pk == third.pk
        assert Setting.objects.filter(name="RACE_DUP").count() == 1

    def test_create_race_with_committed_row_retries_as_update(self, monkeypatch):
        """并发首写模拟：首查读不到对方已提交的同名行，插入撞 name 唯一约束后
        回滚重查、改走更新分支——不产生双行，也不向调用方抛出数据库异常。"""
        Setting.objects.create(name="RACE_LOST", value=json.dumps({"winner": True}), category="default")

        real_select_for_update = Setting.objects.select_for_update
        missed = []

        def racy_select_for_update(*args, **kwargs):
            queryset = real_select_for_update(*args, **kwargs)
            if not missed:
                # 模拟第一遍查询发生在对方提交之前：查不到既有行
                missed.append(True)
                return queryset.none()
            return queryset

        monkeypatch.setattr(Setting.objects, "select_for_update", racy_select_for_update)

        changed, setting = Setting.update_or_create(name="RACE_LOST", value={"mine": 1})
        assert changed is True
        rows = Setting.objects.filter(name="RACE_LOST")
        assert rows.count() == 1
        assert setting.pk == rows.get().pk
        assert setting.cleaned_value == {"mine": 1}

    def test_double_unique_conflict_falls_back_to_existing_row(self, monkeypatch):
        """连续两次插入均撞唯一约束的极端并发：不再尝试写入，按未变更返回既有行。"""
        existing = Setting.objects.create(name="RACE_TWICE", value=json.dumps({"kept": True}))

        real_select_for_update = Setting.objects.select_for_update
        misses = []

        def always_missing_select_for_update(*args, **kwargs):
            queryset = real_select_for_update(*args, **kwargs)
            if len(misses) < 2:
                misses.append(True)
                return queryset.none()
            return queryset

        monkeypatch.setattr(Setting.objects, "select_for_update", always_missing_select_for_update)

        changed, setting = Setting.update_or_create(name="RACE_TWICE", value={"other": 1})
        assert changed is False
        assert setting is not None
        assert setting.pk == existing.pk
        assert Setting.objects.filter(name="RACE_TWICE").count() == 1
        assert Setting.objects.get(name="RACE_TWICE").cleaned_value == {"kept": True}


class TestBasicSettingSerializerHooks:
    def test_validate_site_url_keeps_empty_and_strips_trailing_slash(self):
        from settings.serializers.basic import BasicSettingSerializer

        serializer = BasicSettingSerializer()
        # 留空 = 未配置，原样落库（不伪造回环地址）
        assert serializer.validate_SITE_URL("") == ""
        assert serializer.validate_SITE_URL("http://x.local/") == "http://x.local"

    def test_post_save_notifies_only_on_permission_field_change(self):
        from settings.serializers.basic import BasicSettingSerializer, invalid_user_cache_signal

        received = []

        def receiver(sender, **kwargs):
            received.append(kwargs)

        invalid_user_cache_signal.connect(receiver, weak=False)
        try:
            serializer = BasicSettingSerializer()
            serializer.change_fields = ["PERMISSION_FIELD_ENABLED"]
            serializer.post_save()
            assert received and received[0]["user_pk"] == "*"

            received.clear()
            serializer.change_fields = ["EMAIL_ENABLED"]
            serializer.post_save()
            assert received == []
        finally:
            invalid_user_cache_signal.disconnect(receiver)
