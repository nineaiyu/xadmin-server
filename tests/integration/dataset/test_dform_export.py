# -*- coding: utf-8 -*-
"""动态表单提交导出：复用导出框架 + 按表单 schema 展开动态数据列。

口径：
- 固定列 = 提交号/表单/提交人/提交时间；动态列 = 涉及表单 schema 的字段（key 去重）；
- 导出范围跟随列表筛选与 creator 隔离（普通用户只导出本人提交）。
"""

import pytest
from rest_framework.test import APIClient

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from tests.responses import read_text

pytestmark = pytest.mark.django_db

EXPORT_URL = "/api/dataset/dynamic-form-submissions/export-data"


def _make_form(**overrides):
    defaults = {
        "name": "入职登记",
        "schema": {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
                {"key": "phone", "label": "联系电话", "type": "input"},
            ]
        },
    }
    defaults.update(overrides)
    return DynamicForm.objects.create(**defaults)


def _csv_lines(resp) -> list:
    return read_text(resp).strip().splitlines()


@pytest.fixture
def export_menu(db, menu_factory, role):
    """普通用户的导出权限点（权限菜单驱动；超管不需要）。"""
    menu = menu_factory(
        "exportData:FormMySubmission",
        path="api/dataset/dynamic-form-submissions/export-data$",
        method="GET",
    )
    role.menu.add(menu)
    return menu


class TestSubmissionExport:
    def test_csv_expands_dynamic_columns(self, auth_client, superuser):
        form = _make_form()
        DynamicFormSubmission.objects.create(
            form=form, data={"name": "张三", "phone": "13800000000"}, creator=superuser
        )
        resp = auth_client.get(f"{EXPORT_URL}?type=csv")
        assert resp.status_code == 200
        lines = _csv_lines(resp)
        header = lines[0]
        assert "姓名(name)" in header
        assert "联系电话(phone)" in header
        assert "(pk)" in header
        assert "form_name)" in header and "creator_name)" in header and "created_time)" in header
        assert "张三" in lines[1]
        assert "13800000000" in lines[1]

    def test_export_merges_multiple_forms_schema(self, auth_client, superuser):
        first = _make_form(name="表单A", schema={"fields": [{"key": "a", "label": "甲", "type": "input"}]})
        second = _make_form(name="表单B", schema={"fields": [{"key": "b", "label": "乙", "type": "input"}]})
        DynamicFormSubmission.objects.create(form=first, data={"a": "1"}, creator=superuser)
        DynamicFormSubmission.objects.create(form=second, data={"b": "2"}, creator=superuser)
        resp = auth_client.get(f"{EXPORT_URL}?type=csv")
        header = _csv_lines(resp)[0]
        assert "甲(a)" in header and "乙(b)" in header

    def test_export_respects_creator_isolation(self, auth_client, superuser, normal_user, export_menu):
        form = _make_form()
        DynamicFormSubmission.objects.create(form=form, data={"name": "超管"}, creator=superuser)
        DynamicFormSubmission.objects.create(form=form, data={"name": "张三"}, creator=normal_user)

        client = APIClient()
        client.force_authenticate(user=normal_user)
        resp = client.get(f"{EXPORT_URL}?type=csv")
        assert resp.status_code == 200
        content = read_text(resp)
        assert "张三" in content
        assert "超管" not in content

    def test_export_follows_form_filter(self, auth_client, superuser):
        first = _make_form(name="表单A", schema={"fields": [{"key": "a", "label": "甲", "type": "input"}]})
        second = _make_form(name="表单B", schema={"fields": [{"key": "b", "label": "乙", "type": "input"}]})
        DynamicFormSubmission.objects.create(form=first, data={"a": "1"}, creator=superuser)
        DynamicFormSubmission.objects.create(form=second, data={"b": "2"}, creator=superuser)
        resp = auth_client.get(f"{EXPORT_URL}?type=csv&form={first.pk}")
        lines = _csv_lines(resp)
        assert "甲(a)" in lines[0]
        assert "乙(b)" not in lines[0]
        assert len(lines) == 2  # 表头 + 1 行


class TestSensitiveExportAfterSchemaChange:
    """敏感性按「任一版本敏感即敏感」：字段改为非敏感后历史提交的敏感值仍须掩码。"""

    def test_historical_sensitive_value_stays_masked(self, normal_user, export_menu):
        v1_fields = [
            {"key": "name", "label": "姓名", "type": "input"},
            {"key": "phone", "label": "电话", "type": "input", "sensitive": True},
        ]
        form = DynamicForm.objects.create(
            name="敏感改版表单",
            schema={"fields": v1_fields},
            schema_history=[{"version": 1, "schema": {"fields": v1_fields}}],
            schema_version=2,
        )
        DynamicFormSubmission.objects.create(
            form=form, data={"name": "张三", "phone": "13800000000"}, schema_version=1, creator=normal_user
        )
        # v2：phone 改为非敏感（当前 schema 不再标记）
        form.schema = {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input"},
                {"key": "phone", "label": "电话", "type": "input", "sensitive": False},
            ]
        }
        form.save(update_fields=["schema", "updated_time"])

        client = APIClient()
        client.force_authenticate(user=normal_user)
        content = read_text(client.get(f"{EXPORT_URL}?type=csv"))

        assert "13800000000" not in content
        assert "******" in content

    def test_cross_form_mixed_sensitivity_masks(self, normal_user, export_menu):
        sensitive_form = _make_form(
            name="敏感表单",
            schema={"fields": [{"key": "phone", "label": "电话", "type": "input", "sensitive": True}]},
        )
        plain_form = _make_form(
            name="普通表单",
            schema={"fields": [{"key": "phone", "label": "电话", "type": "input"}]},
        )
        DynamicFormSubmission.objects.create(form=sensitive_form, data={"phone": "13900000000"}, creator=normal_user)
        DynamicFormSubmission.objects.create(form=plain_form, data={"phone": "13700000000"}, creator=normal_user)

        client = APIClient()
        client.force_authenticate(user=normal_user)
        content = read_text(client.get(f"{EXPORT_URL}?type=csv"))

        assert "13900000000" not in content
        assert "13700000000" not in content
