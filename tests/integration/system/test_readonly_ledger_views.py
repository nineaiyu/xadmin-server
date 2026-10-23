# -*- coding: utf-8 -*-
"""只读台账视图守护：字段管理 / 系统设置不提供任何写入口。

历史缺陷：两个视图都继承 ``ImportExportDataAction``（自带 Create / Update 动作），
展示序列化器全字段只读也拦不住空表单 POST —— 字段注册表会落一条
``name='' label=''`` 的空行；设置总表可被写入任意 name 并显式 ``encrypted=false``，
绕过 typed 设置页的白名单与敏感键加密。两页定位是「只读台账」，写入只走
typed 设置页与 sync 命令。
"""

import pytest

from settings.models import Setting
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

FIELD_URL = "/api/system/field"
SETTING_URL = "/api/settings/setting"


class TestModelFieldReadOnly:
    def test_create_is_not_exposed(self, auth_client):
        response = auth_client.post(FIELD_URL, {}, format="json")
        assert response.status_code == 405
        assert not ModelLabelField.objects.exists(), "空表单不得再落 name='' label='' 的空行"

    def test_update_is_not_exposed(self, auth_client):
        row = ModelLabelField.objects.create(name="identity.userinfo", label="用户信息")
        response = auth_client.patch(f"{FIELD_URL}/{row.pk}", {"label": "改名"}, format="json")
        assert response.status_code == 405
        row.refresh_from_db()
        assert row.label == "用户信息"

    def test_import_endpoints_are_removed(self, auth_client):
        for action in ("import-data", "import-validate", "import-headers", "import-async"):
            response = auth_client.post(f"{FIELD_URL}/{action}", {}, format="json")
            assert response.status_code in (404, 405), f"{action} 不应再存在"

    def test_delete_is_not_exposed(self, auth_client):
        """删除会把字段从所有角色的字段权限白名单里连带抹掉（M2M 级联）：入口关闭。"""
        row = ModelLabelField.objects.create(name="identity.userinfo", label="用户信息")

        deleted = auth_client.delete(f"{FIELD_URL}/{row.pk}")
        batch = auth_client.post(f"{FIELD_URL}/batch-destroy", {"pks": [str(row.pk)]}, format="json")

        assert deleted.status_code == 405
        assert batch.status_code in (404, 405)
        assert ModelLabelField.objects.filter(pk=row.pk).exists()

    def test_list_and_export_still_work(self, auth_client):
        ModelLabelField.objects.create(name="identity.userinfo", label="用户信息")
        assert auth_client.get(FIELD_URL).data["code"] == 1000
        response = auth_client.get(f"{FIELD_URL}/export-data?type=csv")
        assert response.status_code == 200


class TestSettingReadOnly:
    def test_create_is_not_exposed(self, auth_client):
        """不能绕过 typed 设置页直接写入任意 name（含 encrypted=false 的敏感键）。"""
        response = auth_client.post(
            SETTING_URL,
            {"name": "SITE_URL", "value": "https://evil.example.com", "encrypted": False},
            format="json",
        )
        assert response.status_code == 405
        assert not Setting.objects.filter(name="SITE_URL").exists()

    def test_update_and_delete_are_not_exposed(self, auth_client):
        row = Setting.objects.create(name="SITE_URL", value='"https://ok.example.com"')

        patched = auth_client.patch(f"{SETTING_URL}/{row.pk}", {"value": '"x"'}, format="json")
        assert patched.status_code == 405
        deleted = auth_client.delete(f"{SETTING_URL}/{row.pk}")
        assert deleted.status_code == 405

        row.refresh_from_db()
        assert row.value == '"https://ok.example.com"'

    def test_import_and_batch_destroy_are_removed(self, auth_client):
        assert auth_client.post(f"{SETTING_URL}/import-data", {}, format="json").status_code in (404, 405)
        assert auth_client.post(f"{SETTING_URL}/batch-destroy", {"pks": []}, format="json").status_code in (404, 405)

    def test_list_and_export_still_work(self, auth_client):
        Setting.objects.create(name="SITE_URL", value='"https://ok.example.com"')
        assert auth_client.get(SETTING_URL).data["code"] == 1000
        response = auth_client.get(f"{SETTING_URL}/export-data?type=csv")
        assert response.status_code == 200
