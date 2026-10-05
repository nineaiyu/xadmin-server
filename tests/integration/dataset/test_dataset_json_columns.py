# -*- coding: utf-8 -*-
"""数据集 JSON 路径列集成测试。

覆盖：明细输出（行键 = 列声明 / 缺键为空不扰其它列）、筛选（文本与数值语义）、
排序、分组与 sum/avg 聚合、JSON 趋势 fail-closed、字段权限按根字段收敛、
保存侧坏声明拒绝（HTTP 400）。
"""

import pytest
from django.conf import settings as dj_settings
from django.core.exceptions import ValidationError

from dataset.models import Dataset
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dataset import aggregate_dataset, execute_dataset
from identity.models import UserRole
from system.models import FieldPermission, Menu, MenuMeta, ModelLabelField

pytestmark = pytest.mark.django_db

BOUND = "dataset.dynamicformsubmission"
DATASET_URL = "/api/dataset/datasets"


@pytest.fixture(autouse=True)
def _data_permission_on(settings):
    dj_settings.PERMISSION_DATA_ENABLED = True


@pytest.fixture
def registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=BOUND,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "表单提交"},
    )
    for name in ("data", "schema_version", "created_time"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def submissions(registry, superuser):
    """三条提交：两条带 amount（3 / 10），一条缺 amount（缺键行为）。"""
    form = DynamicForm.objects.create(
        name="E2E-JSON列",
        schema={"fields": [{"key": "kind", "label": "类别", "type": "select"}]},
        is_active=True,
        creator=superuser,
    )
    for data in ({"amount": 3, "kind": "a"}, {"amount": 10, "kind": "b"}, {"kind": "a"}):
        DynamicFormSubmission.objects.create(form=form, data=data, creator=superuser)
    return form


@pytest.fixture
def dataset(registry, superuser):
    return Dataset.objects.create(
        name="表单JSON列",
        bound_model=BOUND,
        columns=["schema_version", "data.kind", "data.amount|number"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


class TestExecuteJsonColumns:
    def test_rows_use_declared_keys(self, dataset, submissions, superuser):
        result = execute_dataset(dataset, superuser)
        assert result["columns"] == ["schema_version", "data.kind", "data.amount|number"]
        assert result["total"] == 3
        kinds = [row["data.kind"] for row in result["rows"]]
        assert sorted(kinds) == ["a", "a", "b"]
        amounts = sorted(v for v in (row["data.amount|number"] for row in result["rows"]) if v is not None)
        # 缺键行不参与数值列（None），不影响其它行
        assert amounts == [3.0, 10.0]
        assert any(row["data.amount|number"] is None for row in result["rows"])

    def test_filter_text_column(self, dataset, submissions, superuser):
        dataset.filters = [{"field": "data.kind", "op": "exact", "value": "a"}]
        result = execute_dataset(dataset, superuser)
        assert result["total"] == 2
        assert {row["data.kind"] for row in result["rows"]} == {"a"}

    def test_filter_numeric_semantics(self, dataset, submissions, superuser):
        """``|number`` 列的比较是数值语义：10 必须命中 gte 5（字符串比较会把 "10" 判为更小）。"""
        dataset.filters = [{"field": "data.amount|number", "op": "gte", "value": 5}]
        result = execute_dataset(dataset, superuser)
        assert [row["data.kind"] for row in result["rows"]] == ["b"]

    def test_ordering_numeric_desc(self, dataset, submissions, superuser):
        dataset.ordering = "-data.amount|number"
        result = execute_dataset(dataset, superuser)
        first = result["rows"][0]
        assert first["data.amount|number"] == 10.0


class TestAggregateJsonColumns:
    def test_group_by_json_text(self, dataset, submissions, superuser):
        result = aggregate_dataset(dataset, superuser, group_by="data.kind")
        assert result["name"] == "data.kind"
        assert sorted((item["name"], item["value"]) for item in result["series"]) == [("a", 2), ("b", 1)]

    def test_sum_json_number(self, dataset, submissions, superuser):
        grouped = aggregate_dataset(
            dataset, superuser, group_by="data.kind", metric="sum", value_field="data.amount|number"
        )
        assert sorted((item["name"], item["value"]) for item in grouped["series"]) == [("a", 3.0), ("b", 10.0)]
        total = aggregate_dataset(dataset, superuser, group_by="", metric="sum", value_field="data.amount|number")
        assert total["series"][0]["value"] == 13.0

    def test_sum_rejects_unannotated_json(self, dataset, submissions, superuser):
        with pytest.raises(ValidationError):
            aggregate_dataset(dataset, superuser, group_by="data.kind", metric="sum", value_field="data.amount")

    def test_rejects_json_trend(self, dataset, submissions, superuser):
        """JSON 路径的日期趋势在本段不支持：fail-closed。"""
        with pytest.raises(ValidationError):
            aggregate_dataset(dataset, superuser, group_by="data.kind", metric="count", date_trunc="day")


class TestJsonColumnPermissions:
    def _grant_role_with_field(self, normal_user, registry, field_names):
        role = UserRole.objects.create(name=f"json-role-{normal_user.username}", code=f"json-{normal_user.pk}")
        normal_user.roles.add(role)
        menu = Menu.objects.create(
            name="list:Dataset",
            path="api/dataset/datasets$",
            method="GET",
            menu_type=Menu.MenuChoices.PERMISSION,
            meta=MenuMeta.objects.create(title="数据集"),
        )
        permission = FieldPermission.objects.create(role=role, menu=menu)
        permission.field.set([ModelLabelField.objects.get(name=name, parent=registry) for name in field_names])
        return role

    def test_json_column_trimmed_without_root_permission(self, dataset, submissions, registry, normal_user):
        """字段权限无 ``data`` 时，JSON 路径列按根字段被裁（保留 schema_version 列）。"""
        self._grant_role_with_field(normal_user, registry, ["schema_version"])
        result = execute_dataset(dataset, normal_user)
        assert result["columns"] == ["schema_version"]

    def test_json_column_visible_with_root_permission(self, dataset, submissions, registry, normal_user):
        self._grant_role_with_field(normal_user, registry, ["data", "schema_version"])
        result = execute_dataset(dataset, normal_user)
        assert result["columns"] == ["schema_version", "data.kind", "data.amount|number"]

    def test_aggregate_rejects_hidden_root(self, dataset, submissions, registry, normal_user):
        """聚合分组字段为 JSON 路径且根字段无权限 → 报错（不泄露隐藏字段分布）。"""
        self._grant_role_with_field(normal_user, registry, ["schema_version"])
        with pytest.raises(ValidationError):
            aggregate_dataset(dataset, normal_user, group_by="data.kind")


class TestSaveValidation:
    @pytest.mark.parametrize(
        "column",
        ["data.created|datetime", "data.a.b", "schema_version.kind", "created_time|number"],
    )
    def test_rejects_invalid_json_columns(self, auth_client, registry, column):
        response = auth_client.post(
            DATASET_URL,
            {"name": f"坏列-{column}", "bound_model": BOUND, "columns": [column]},
            format="json",
        )
        assert response.status_code == 400

    def test_accepts_json_columns(self, auth_client, registry):
        response = auth_client.post(
            DATASET_URL,
            {
                "name": "JSON列合法",
                "bound_model": BOUND,
                "columns": ["schema_version", "data.kind", "data.amount|number"],
                "ordering": "-data.amount|number",
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.json()["code"] == 1000

    def test_meta_exposes_json_fields(self, auth_client, registry):
        body = auth_client.get(f"{DATASET_URL}/meta").json()
        assert "data" in body["data"]["json_fields"][BOUND]

    def test_rejects_json_date_field(self, auth_client, registry):
        response = auth_client.post(
            DATASET_URL,
            {
                "name": "JSON趋势字段",
                "bound_model": BOUND,
                "columns": ["data.kind"],
                "config": {"date_field": "data.created"},
            },
            format="json",
        )
        assert response.status_code == 400
