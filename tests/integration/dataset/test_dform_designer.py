# -*- coding: utf-8 -*-
"""动态表单设计器升级（API 层）：schema 规范化 / 联动规则 / 版本化与回滚。

口径：
- 写入侧 schema 规范化（顶层未声明键丢弃；联动规则校验后落库）；
- schema 实质变更 → 版本 +1 并归档变更前快照；同内容保存不产生新版本；
- 历史端点返回版本全文；回滚应用历史 schema 并生成新版本（历史保留）；
- 提交记录保存时的表单版本（校验始终按提交当时的 schema）。
"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission

pytestmark = pytest.mark.django_db

FORMS_URL = "/api/dataset/dynamic-forms"
SUBMISSIONS_URL = "/api/dataset/dynamic-form-submissions"

SCHEMA_V1 = {
    "fields": [
        {"key": "kind", "label": "类型", "type": "select", "options": ["A", "B"], "required": True},
        {"key": "reason", "label": "说明", "type": "input", "required": True},
    ],
    "linkages": [{"target": "reason", "field": "kind", "op": "eq", "value": "A", "effect": "optional"}],
}


def _create_form(client, name="设计器表单", schema=None):
    resp = client.post(FORMS_URL, {"name": name, "schema": schema or SCHEMA_V1}, format="json")
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]


class TestSchemaNormalization:
    def test_create_normalizes_and_versions(self, auth_client):
        created = _create_form(
            auth_client,
            schema={
                "fields": SCHEMA_V1["fields"],
                "linkages": SCHEMA_V1["linkages"],
                "unknown_key": {"x": 1},
            },
        )
        assert created["schema_version"] == 1
        assert set(created["schema"]) == {"fields", "linkages"}
        assert created["schema"]["linkages"][0]["effect"] == "optional"

    def test_invalid_linkage_rejected(self, auth_client):
        resp = auth_client.post(
            FORMS_URL,
            {
                "name": "非法联动",
                "schema": {
                    "fields": SCHEMA_V1["fields"],
                    "linkages": [{"target": "nope", "field": "kind", "op": "eq", "value": "A", "effect": "hide"}],
                },
            },
            format="json",
        )
        assert resp.data["code"] != 1000
        # 断言用户数据（非法字段名）回显，不依赖 i18n 文案（本地 .mo 与 CI 语言不同）
        assert "nope" in str(resp.data)


class TestSchemaVersioning:
    def test_update_bumps_version_and_archives(self, auth_client):
        created = _create_form(auth_client, name="版本表单")
        pk = created["pk"]

        changed = {
            "fields": SCHEMA_V1["fields"] + [{"key": "extra", "label": "附加", "type": "input"}],
            "linkages": [],
        }
        resp = auth_client.patch(f"{FORMS_URL}/{pk}", {"schema": changed}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["schema_version"] == 2

        form = DynamicForm.objects.get(pk=pk)
        assert len(form.schema_history) == 1
        archived = form.schema_history[0]
        assert archived["version"] == 1
        assert archived["schema"] == SCHEMA_V1

    def test_same_schema_does_not_bump(self, auth_client):
        created = _create_form(auth_client, name="幂等表单")
        pk = created["pk"]
        resp = auth_client.patch(f"{FORMS_URL}/{pk}", {"schema": created["schema"]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["schema_version"] == 1
        assert DynamicForm.objects.get(pk=pk).schema_history == []

    def test_schema_history_endpoint(self, auth_client):
        created = _create_form(auth_client, name="历史表单")
        pk = created["pk"]
        auth_client.patch(
            f"{FORMS_URL}/{pk}",
            {"schema": {"fields": SCHEMA_V1["fields"][:1]}},
            format="json",
        )
        resp = auth_client.get(f"{FORMS_URL}/{pk}/schema-history")
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["current"] == 2
        assert [item["version"] for item in data["history"]] == [1]
        assert data["history"][0]["schema"]["fields"][0]["key"] == "kind"

    def test_rollback_applies_history_and_bumps(self, auth_client):
        created = _create_form(auth_client, name="回滚表单")
        pk = created["pk"]
        auth_client.patch(f"{FORMS_URL}/{pk}", {"schema": {"fields": SCHEMA_V1["fields"][:1]}}, format="json")

        resp = auth_client.post(f"{FORMS_URL}/{pk}/rollback", {"version": 1}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["schema_version"] == 3
        form = DynamicForm.objects.get(pk=pk)
        assert form.schema["fields"][0]["key"] == "kind"
        assert len(form.schema["fields"]) == 2
        # 历史保留：v2（被替换掉的一字段版本）与 v1 都在
        assert sorted(item["version"] for item in form.schema_history) == [1, 2]

    def test_rollback_unknown_version_rejected(self, auth_client):
        created = _create_form(auth_client, name="回滚缺版本")
        resp = auth_client.post(f"{FORMS_URL}/{created['pk']}/rollback", {"version": 9}, format="json")
        assert resp.data["code"] != 1000
        assert DynamicForm.objects.get(pk=created["pk"]).schema_version == 1


class TestListSchemaTrimming:
    def test_list_reports_field_count_only(self, auth_client):
        """列表行不回传 schema 全文：只带字段数元数据，详情仍取全文。"""
        created = _create_form(auth_client, name="轻列表表单")
        listed = auth_client.get(FORMS_URL)
        row = next(item for item in listed.data["data"]["results"] if item["pk"] == created["pk"])
        assert "schema" not in row
        assert row["schema_fields_count"] == len(SCHEMA_V1["fields"])

        detail = auth_client.get(f"{FORMS_URL}/{created['pk']}")
        assert detail.data["code"] == 1000, detail.data
        assert detail.data["data"]["schema"] == SCHEMA_V1

    def test_template_list_reports_field_count(self, auth_client):
        """模板列表（kind=templates）与表单列表同口径：不回传 schema 全文。"""
        auth_client.post(
            FORMS_URL,
            {
                "name": "模板-轻列表",
                "is_template": True,
                "is_active": False,
                "schema": SCHEMA_V1,
            },
            format="json",
        )
        listed = auth_client.get(f"{FORMS_URL}?kind=templates")
        rows = listed.data["data"]["results"]
        assert len(rows) == 1
        assert "schema" not in rows[0]
        assert rows[0]["schema_fields_count"] == len(SCHEMA_V1["fields"])


class TestSubmissionVersionAndLinkage:
    def test_submission_records_current_schema_version(self, auth_client):
        created = _create_form(auth_client, name="提交版本表单")
        pk = created["pk"]
        submit = auth_client.post(
            f"{SUBMISSIONS_URL}",
            {"form": pk, "data": {"kind": "A"}},
            format="json",
        )
        assert submit.data["code"] == 1000, submit.data
        assert submit.data["data"]["schema_version"] == 1

        auth_client.patch(f"{FORMS_URL}/{pk}", {"schema": {"fields": SCHEMA_V1["fields"][:1]}}, format="json")
        submit2 = auth_client.post(f"{SUBMISSIONS_URL}", {"form": pk, "data": {"kind": "B"}}, format="json")
        assert submit2.data["code"] == 1000, submit2.data
        assert submit2.data["data"]["schema_version"] == 2
        assert DynamicFormSubmission.objects.filter(form_id=pk).count() == 2

    def test_hidden_field_skipped_and_dropped(self, auth_client):
        """联动 hide：目标字段必填被跳过，且提交值不落库。"""
        schema = {
            "fields": SCHEMA_V1["fields"],
            "linkages": [{"target": "reason", "field": "kind", "op": "eq", "value": "A", "effect": "hide"}],
        }
        created = _create_form(auth_client, name="隐藏联动表单", schema=schema)
        resp = auth_client.post(
            f"{SUBMISSIONS_URL}",
            {"form": created["pk"], "data": {"kind": "A", "reason": "不应落库"}},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        submission = DynamicFormSubmission.objects.get(pk=resp.data["data"]["pk"])
        assert submission.data["reason"] is None

    def test_dynamic_required_enforced_via_api(self, auth_client):
        schema = {
            "fields": SCHEMA_V1["fields"],
            "linkages": [{"target": "reason", "field": "kind", "op": "eq", "value": "B", "effect": "require"}],
        }
        created = _create_form(auth_client, name="动态必填表单", schema=schema)
        # 基础定义 reason 必填：kind=A 时联动不改变（仍必填），因此这里只验证 kind=B 的显式必填路径
        resp = auth_client.post(f"{SUBMISSIONS_URL}", {"form": created["pk"], "data": {"kind": "B"}}, format="json")
        assert resp.data["code"] != 1000

    def test_dynamic_optional_relaxes_base_required(self, auth_client):
        created = _create_form(auth_client, name="动态非必填表单")
        resp = auth_client.post(
            f"{SUBMISSIONS_URL}",
            {"form": created["pk"], "data": {"kind": "A"}},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
