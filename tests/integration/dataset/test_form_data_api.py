# -*- coding: utf-8 -*-
"""表单数据（管理端）：按表单浏览 / 筛选 / 导出全部提交。

口径：
- 取值域不做 creator 隔离，走数据权限编译器——超管全量，非超管按授权行收敛，
  未配置授权即空集（fail-closed）；
- 只读（list / retrieve / export-data / form-options），提交与改动留在「我的填报」；
- 「谁能看」= 页面权限点（本菜单的 list / retrieve / exportData）× 数据权限授权。
"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from system.models import DataPermission
from tests.responses import read_text

pytestmark = pytest.mark.django_db

LIST_URL = "/api/dataset/form-data"
EXPORT_URL = "/api/dataset/form-data/export-data"
FORM_OPTIONS_URL = "/api/dataset/form-data/form-options"
LIST_PERMISSION_PATH = "api/dataset/form-data$"


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


def _make_submission(form, creator, **overrides):
    data = {"name": "张三", "phone": "13800000000"}
    data.update(overrides.pop("data", {}))
    return DynamicFormSubmission.objects.create(form=form, creator=creator, data=data, **overrides)


def _rows(resp) -> list:
    return resp.data["data"]["results"]


def _grant_list(role, menu_factory):
    menu = menu_factory("list:FormData", path=LIST_PERMISSION_PATH, method="GET")
    role.menu.add(menu)
    return menu


def _own_only_permission(user, name="own-form-data"):
    """「仅本人」数据权限：value.user.id 运行时注入当前用户主键。"""
    dp = DataPermission.objects.create(
        name=name,
        rules=[
            {
                "table": "dataset.dynamicformsubmission",
                "field": "creator",
                "type": "value.user.id",
                "match": "exact",
                "value": "*",
                "exclude": False,
            }
        ],
    )
    user.rules.add(dp)
    return dp


def _all_permission(user, name="all-form-data"):
    """「全部数据」数据权限：放行行可见性（与 e2e_seed 的用户列表同款）。"""
    dp = DataPermission.objects.create(
        name=name,
        rules=[
            {
                "table": "dataset.dynamicformsubmission",
                "field": "id",
                "type": "value.all",
                "match": "all",
                "value": "",
                "exclude": False,
            }
        ],
    )
    user.rules.add(dp)
    return dp


class TestFormDataList:
    def test_superuser_sees_all_submissions(self, auth_client, superuser, normal_user):
        """超管全量可见：跨提交人（无 creator 隔离）+ 列表契约字段完整。"""
        form = _make_form()
        _make_submission(form, superuser, data={"name": "超管的提交"})
        _make_submission(form, normal_user, data={"name": "普通用户的提交"})

        resp = auth_client.get(LIST_URL)
        assert resp.status_code == 200
        rows = _rows(resp)
        assert len(rows) == 2
        names = {row["data"]["name"] for row in rows}
        assert names == {"超管的提交", "普通用户的提交"}
        first = rows[0]
        assert first["form_name"] == "入职登记"
        assert first["creator"]["username"] in ("admin", "zhangsan")
        # 列表契约不含详情字段（form_schema / approval_trail 仅 retrieve）
        assert "form_schema" not in first
        assert "approval_trail" not in first

    def test_filter_by_form_and_status(self, auth_client, superuser):
        first = _make_form(name="表单A", schema={"fields": [{"key": "a", "label": "甲", "type": "input"}]})
        second = _make_form(name="表单B", schema={"fields": [{"key": "b", "label": "乙", "type": "input"}]})
        _make_submission(first, superuser, data={"a": "1"})
        _make_submission(second, superuser, data={"b": "2"}, status=DynamicFormSubmission.Status.PENDING)

        resp = auth_client.get(f"{LIST_URL}?form={first.pk}")
        assert [row["form_name"] for row in _rows(resp)] == ["表单A"]

        resp = auth_client.get(f"{LIST_URL}?status=PENDING")
        rows = _rows(resp)
        assert len(rows) == 1
        assert rows[0]["form_name"] == "表单B"
        assert rows[0]["status"]["value"] == "PENDING"

    def test_retrieve_returns_detail_contract(self, auth_client, superuser):
        form = _make_form()
        submission = _make_submission(form, superuser)
        resp = auth_client.get(f"{LIST_URL}/{submission.pk}")
        assert resp.status_code == 200
        data = resp.data["data"]
        assert [field["key"] for field in data["form_schema"]] == ["name", "phone"]
        assert data["approval_trail"] == []
        assert data["instance"] is None


class TestFormOptions:
    def test_returns_non_template_forms_including_inactive(self, auth_client, superuser):
        active = _make_form(name="启用表单")
        inactive = _make_form(name="停用表单", is_active=False)
        _make_form(name="模板表单", is_template=True)

        resp = auth_client.get(FORM_OPTIONS_URL)
        assert resp.status_code == 200
        # 未截断：显式 false + 默认成功 detail（前端不得据此弹提示）
        assert resp.data["truncated"] is False
        pks = {row["pk"] for row in resp.data["data"]}
        assert pks == {active.pk, inactive.pk}
        row = next(item for item in resp.data["data"] if item["pk"] == active.pk)
        assert row["schema"]["fields"][0]["key"] == "name"

    def test_truncated_flag_and_notice(self, auth_client, monkeypatch):
        """超过数据源上限：显式 truncated 标记 + 截断提示，前端只据此提示。"""
        from common.utils.datasource import truncation_detail

        monkeypatch.setattr("common.utils.datasource.DATASOURCE_MAX_ROWS", 1)
        _make_form(name="表单-A")
        _make_form(name="表单-B")

        resp = auth_client.get(FORM_OPTIONS_URL)
        assert resp.data["code"] == 1000
        assert len(resp.data["data"]) == 1
        assert resp.data["truncated"] is True
        assert resp.data["detail"] == truncation_detail()


class TestFormDataExport:
    def test_export_expands_dynamic_columns(self, auth_client, superuser):
        form = _make_form()
        _make_submission(form, superuser)
        resp = auth_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}")
        assert resp.status_code == 200
        lines = read_text(resp).strip().splitlines()
        assert "姓名(name)" in lines[0]
        assert "联系电话(phone)" in lines[0]
        assert "张三" in lines[1]


class TestFormDataSensitiveMask:
    """schema 级 sensitive：非超管列表/详情/导出统一掩码，超管可见原值。"""

    def _form(self):
        return _make_form(
            schema={
                "fields": [
                    {"key": "name", "label": "姓名", "type": "input", "required": True},
                    {"key": "phone", "label": "联系电话", "type": "input", "sensitive": True},
                ]
            }
        )

    def test_list_and_detail_mask_for_non_superuser(
        self, api_client, auth_client, normal_user, role, menu_factory, superuser
    ):
        form = self._form()
        submission = _make_submission(form, superuser)
        _grant_list(role, menu_factory)
        role.menu.add(
            menu_factory(
                "retrieve:FormData",
                path="api/dataset/form-data/(?P<pk>[^/.]+)$",
                method="GET",
            )
        )
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        row = _rows(api_client.get(LIST_URL))[0]
        assert row["data"]["name"] == "张三"
        assert row["data"]["phone"] == "******"
        detail = api_client.get(f"{LIST_URL}/{submission.pk}")
        assert detail.data["data"]["data"]["phone"] == "******"

        # 超管不受掩码影响（api_client/auth_client 是同一个实例，先切回超管）
        api_client.force_authenticate(user=superuser)
        admin_row = _rows(api_client.get(LIST_URL))[0]
        assert admin_row["data"]["phone"] == "13800000000"

    def test_export_masks_for_non_superuser(self, api_client, auth_client, normal_user, role, menu_factory, superuser):
        form = self._form()
        _make_submission(form, superuser)
        role.menu.add(menu_factory("exportData:FormData", path="api/dataset/form-data/export-data$", method="GET"))
        _grant_list(role, menu_factory)
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        content = read_text(api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}"))
        assert "13800000000" not in content
        assert "******" in content

        api_client.force_authenticate(user=superuser)
        admin_content = read_text(api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}"))
        assert "13800000000" in admin_content


class TestFormDataDataPermission:
    def test_without_grant_returns_empty(self, api_client, normal_user, role, menu_factory, superuser):
        """数据权限 fail-closed：有页面权限但无任何授权 → 空集（不泄露行存在性）。"""
        form = _make_form()
        _make_submission(form, superuser)
        _grant_list(role, menu_factory)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(LIST_URL)
        assert resp.status_code == 200
        assert _rows(resp) == []

    def test_own_only_grant_sees_own_rows(self, api_client, normal_user, role, menu_factory, superuser):
        form = _make_form()
        _make_submission(form, superuser, data={"name": "超管的提交"})
        _make_submission(form, normal_user, data={"name": "本人的提交"})
        _grant_list(role, menu_factory)
        _own_only_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        rows = _rows(api_client.get(LIST_URL))
        assert [row["data"]["name"] for row in rows] == ["本人的提交"]

    def test_all_grant_sees_other_users_rows(self, api_client, normal_user, role, menu_factory, superuser):
        """授权「全部数据」后可跨提交人浏览（管理端核心能力）。"""
        form = _make_form()
        _make_submission(form, superuser, data={"name": "超管的提交"})
        _grant_list(role, menu_factory)
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        rows = _rows(api_client.get(LIST_URL))
        assert [row["data"]["name"] for row in rows] == ["超管的提交"]

    def test_data_permission_covers_export(self, api_client, normal_user, role, menu_factory, superuser):
        """导出口径与列表同一取值域：无授权导出不泄露数据，授权后按可见行导出。"""
        form = _make_form()
        _make_submission(form, superuser, data={"name": "超管的提交"})
        menu = menu_factory("exportData:FormData", path="api/dataset/form-data/export-data$", method="GET")
        role.menu.add(menu)
        _grant_list(role, menu_factory)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}")
        assert resp.status_code == 200
        # 无授权：导出内容不包含任何他人数据行（空结果按空文件输出）
        assert "超管的提交" not in read_text(resp)

        _all_permission(normal_user)
        resp = api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}")
        content = read_text(resp)
        assert "姓名(name)" in content
        assert "超管的提交" in content

    def test_missing_menu_permission_denied(self, api_client, normal_user, superuser):
        """无页面权限点（连 list 都未授权）→ 403（运行期权限链拦截）。"""
        form = _make_form()
        _make_submission(form, superuser)
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(LIST_URL)
        assert resp.status_code == 403


class TestFormDataSnapshotWindowFailClosed:
    """版本快照滚出保留窗口的提交：掩码 fail-closed（历史敏感键不因版本滚出而明文）。

    提交版本超出 schema_history 保留窗口时回退「当前 schema + data 兜底」，
    敏感性无法确认——无法确认的动态键一律按敏感掩码（列表/详情/导出同口径），
    窗口内的提交仍按 schema 声明精确掩码。
    """

    def _window_form(self):
        """当前 v30；历史窗口仅含 v29——v10 的提交已滚出快照窗口。"""
        return _make_form(
            schema={
                "fields": [
                    {"key": "name", "label": "姓名", "type": "input"},
                    {"key": "phone", "label": "联系电话", "type": "input", "sensitive": True},
                ]
            },
            schema_version=30,
            schema_history=[{"version": 29, "schema": {"fields": [{"key": "name", "label": "姓名", "type": "input"}]}}],
        )

    def _grant_read(self, role, menu_factory):
        _grant_list(role, menu_factory)
        role.menu.add(menu_factory("retrieve:FormData", path="api/dataset/form-data/(?P<pk>[^/.]+)$", method="GET"))

    def test_out_of_window_submission_masked_in_list_and_detail(
        self, api_client, auth_client, normal_user, role, menu_factory, superuser
    ):
        form = self._window_form()
        rolled_out = DynamicFormSubmission.objects.create(
            form=form,
            creator=superuser,
            schema_version=10,
            data={"name": "张三", "phone": "13800000000", "legacy": "机密"},
        )
        current = DynamicFormSubmission.objects.create(
            form=form,
            creator=superuser,
            schema_version=30,
            data={"name": "李四", "phone": "13900000000"},
        )
        self._grant_read(role, menu_factory)
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        rows = {row["pk"]: row for row in _rows(api_client.get(LIST_URL))}
        # 滚出窗口：无法确认敏感性 → 全部动态键掩码（含当前 schema 声明非敏感的 name
        # 与快照兜底补出的 legacy）
        assert rows[str(rolled_out.pk)]["data"] == {"name": "******", "phone": "******", "legacy": "******"}
        # 窗口内（当前版本）：按 schema 声明精确掩码，非敏感键保持可见
        assert rows[str(current.pk)]["data"] == {"name": "李四", "phone": "******"}

        detail = api_client.get(f"{LIST_URL}/{rolled_out.pk}")
        assert detail.data["data"]["data"] == {"name": "******", "phone": "******", "legacy": "******"}
        # schema 快照兜底补出的未知键带 sensitive 标记（fail-closed）
        legacy_item = next(item for item in detail.data["data"]["form_schema"] if item["key"] == "legacy")
        assert legacy_item["sensitive"] is True

        api_client.force_authenticate(user=superuser)
        admin_rows = {row["pk"]: row for row in _rows(api_client.get(LIST_URL))}
        assert admin_rows[str(rolled_out.pk)]["data"]["phone"] == "13800000000"

    def test_out_of_window_submission_masked_in_export(
        self, api_client, auth_client, normal_user, role, menu_factory, superuser
    ):
        form = self._window_form()
        DynamicFormSubmission.objects.create(
            form=form,
            creator=superuser,
            schema_version=10,
            data={"name": "张三", "phone": "13800000000"},
        )
        role.menu.add(menu_factory("exportData:FormData", path="api/dataset/form-data/export-data$", method="GET"))
        _grant_list(role, menu_factory)
        _all_permission(normal_user)

        api_client.force_authenticate(user=normal_user)
        content = read_text(api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}"))
        assert "张三" not in content
        assert "13800000000" not in content
        assert "******" in content

        api_client.force_authenticate(user=superuser)
        admin_content = read_text(api_client.get(f"{EXPORT_URL}?type=csv&form={form.pk}"))
        assert "张三" in admin_content


class TestFormDataWriteDenied:
    def test_readonly_endpoints_have_no_write_actions(self, auth_client, superuser):
        """只读边界：管理端不提供提交/修改/删除（405/404，而非静默成功）。"""
        form = _make_form()
        submission = _make_submission(form, superuser)

        assert auth_client.post(LIST_URL, {"form": form.pk, "data": {}}, format="json").status_code == 405
        assert auth_client.patch(f"{LIST_URL}/{submission.pk}", {"data": {}}, format="json").status_code == 405
        assert auth_client.delete(f"{LIST_URL}/{submission.pk}").status_code == 405
