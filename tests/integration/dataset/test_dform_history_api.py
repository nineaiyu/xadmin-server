# -*- coding: utf-8 -*-
"""历史字段可见性（ADR-070）集成测试：改版删除字段后，历史提交的值仍可回看与导出。

覆盖：我的填报详情 / 表单数据详情（schema 按提交版本渲染）、两个导出口径的列合并
（历史字段标注）、表单数据「选择表单」的 schema 合并口径。
"""

import pytest

from dataset.utils.dform_history import HISTORICAL_MARK

pytestmark = pytest.mark.django_db

FORMS_URL = "/api/dataset/dynamic-forms"
SUBMISSIONS_URL = "/api/dataset/dynamic-form-submissions"
FORM_DATA_URL = "/api/dataset/form-data"


@pytest.fixture
def versioned_scene(auth_client):
    """v1 表单提交一条 → 改 schema 删掉字段 b（生成 v1 快照）→ 当前 v2 = [a, c]。"""
    form = auth_client.post(
        FORMS_URL,
        {
            "name": "历史字段表单-API",
            "is_active": True,
            "schema": {
                "fields": [
                    {"key": "a", "label": "甲", "type": "input"},
                    {"key": "b", "label": "乙", "type": "input"},
                ]
            },
        },
        format="json",
    ).json()["data"]
    submission = auth_client.post(
        SUBMISSIONS_URL,
        {"form": form["pk"], "data": {"a": "A1", "b": "B1"}},
        format="json",
    ).json()["data"]
    updated = auth_client.patch(
        f"{FORMS_URL}/{form['pk']}",
        {
            "schema": {
                "fields": [{"key": "a", "label": "甲", "type": "input"}, {"key": "c", "label": "丙", "type": "input"}]
            }
        },
        format="json",
    ).json()["data"]
    assert updated["schema_version"] == 2
    return {"form": updated, "submission": submission}


class TestDetailSchemaByVersion:
    def test_my_submission_detail(self, auth_client, versioned_scene):
        resp = auth_client.get(f"{SUBMISSIONS_URL}/{versioned_scene['submission']['pk']}")
        fields = resp.json()["data"]["form_schema"]
        assert [item["key"] for item in fields] == ["a", "b"]
        assert next(item for item in fields if item["key"] == "b")["historical"] is True

    def test_form_data_detail(self, auth_client, versioned_scene):
        resp = auth_client.get(f"{FORM_DATA_URL}/{versioned_scene['submission']['pk']}")
        fields = resp.json()["data"]["form_schema"]
        assert [item["key"] for item in fields] == ["a", "b"]
        assert f"乙{HISTORICAL_MARK}" == next(item for item in fields if item["key"] == "b")["label"]


class TestExportColumns:
    def test_my_submission_export_keeps_historical_column(self, auth_client, versioned_scene):
        resp = auth_client.get(f"{SUBMISSIONS_URL}/export-data?type=csv")
        assert resp.status_code == 200
        header = resp.content.decode("utf-8-sig").strip().splitlines()[0]
        assert "甲(a)" in header
        assert f"乙{HISTORICAL_MARK}(b)" in header
        # 历史值仍在导出数据行里
        assert "B1" in resp.content.decode("utf-8-sig")

    def test_form_data_export_keeps_historical_column(self, auth_client, versioned_scene):
        resp = auth_client.get(f"{FORM_DATA_URL}/export-data?type=csv")
        assert resp.status_code == 200
        header = resp.content.decode("utf-8-sig").strip().splitlines()[0]
        assert f"乙{HISTORICAL_MARK}(b)" in header


class TestFormOptionsMergedSchema:
    def test_picker_schema_contains_historical_field(self, auth_client, versioned_scene):
        resp = auth_client.get(f"{FORM_DATA_URL}/form-options")
        option = next(item for item in resp.json()["data"] if item["pk"] == versioned_scene["form"]["pk"])
        fields = option["schema"]["fields"]
        assert [item["key"] for item in fields] == ["a", "c", "b"]
        assert next(item for item in fields if item["key"] == "b")["historical"] is True

    def test_my_fill_available_forms_keeps_current_only(self, auth_client, versioned_scene):
        """填报页（available-forms）维持当前 schema：历史字段不回灌填报入口。"""
        resp = auth_client.get(f"{SUBMISSIONS_URL}/available-forms")
        forms = resp.json()["data"]
        target = next(item for item in forms if item["pk"] == versioned_scene["form"]["pk"])
        keys = [item["key"] for item in target["schema"]["fields"]]
        assert keys == ["a", "c"]
