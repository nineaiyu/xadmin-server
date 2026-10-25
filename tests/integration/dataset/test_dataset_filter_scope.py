# -*- coding: utf-8 -*-
"""数据集请求级 filters 的字段权限收敛与 JSON 路径注解。

两块安全 / 稳定性边界：

- **字段权限收敛**：``execute`` / ``aggregate`` 的 ``filters`` 来自请求方，必须与
  浏览者可见字段求交——否则无该列字段权限的用户可用过滤条件反复二分，推断隐藏列
  分布（聚合侧 group_by / value_field 已 fail-closed）。数据集定义内的 filters 是
  创建者的取数范围，不随浏览者裁剪。
- **JSON 路径按需注解**：filter / ordering 引用的 JSON 路径不要求在 ``columns`` 内
  （按维度值取明细本就如此），缺注解会抛 FieldError 变成 500；条件条数另有上限。
"""

import uuid

import pytest
from django.conf import settings as dj_settings

from dataset.models import Dataset
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dataset import FILTER_LIMIT, execute_dataset
from system.models import DataPermission, FieldPermission, Menu, MenuMeta, ModelLabelField

pytestmark = pytest.mark.django_db

DATASET_URL = "/api/dataset/datasets"
USER_BOUND = "identity.userinfo"
JSON_BOUND = "dataset.dynamicformsubmission"
DATA_PERMISSION_ALL_RULES = [
    {"table": USER_BOUND, "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}
]


@pytest.fixture(autouse=True)
def _field_permission_on(settings):
    dj_settings.PERMISSION_FIELD_ENABLED = True


@pytest.fixture
def user_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=USER_BOUND,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "created_time"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def user_dataset(user_registry, superuser):
    return Dataset.objects.create(
        name=f"字段权限数据集-{uuid.uuid4().hex[:6]}",
        bound_model=USER_BOUND,
        columns=["username", "nickname"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def json_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=JSON_BOUND,
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
def json_dataset(json_registry, superuser):
    """列不含 ``data.amount``：过滤条件引用它时必须按需补注解。"""
    return Dataset.objects.create(
        name=f"JSON过滤数据集-{uuid.uuid4().hex[:6]}",
        bound_model=JSON_BOUND,
        columns=["schema_version", "data.kind"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def json_submissions(json_registry, superuser):
    form = DynamicForm.objects.create(
        name=f"JSON过滤-{uuid.uuid4().hex[:6]}",
        schema={"fields": [{"key": "kind", "label": "类别", "type": "select"}]},
        is_active=True,
        creator=superuser,
    )
    for data in ({"amount": 3, "kind": "a"}, {"amount": 10, "kind": "b"}, {"kind": "a"}):
        DynamicFormSubmission.objects.create(form=form, data=data, creator=superuser)
    return form


@pytest.fixture
def execute_menu(normal_user):
    """普通用户的 execute 权限菜单（字段权限行也挂在该菜单下）。"""
    detail = "api/dataset/datasets/(?P<pk>[^/.]+)"
    menus = []
    for name, path, method in (
        ("list:Dataset", "api/dataset/datasets$", "GET"),
        ("execute:Dataset", detail + "/execute$", "POST"),
        ("aggregate:Dataset", detail + "/aggregate$", "POST"),
    ):
        meta = MenuMeta.objects.create(title=name)
        menus.append(
            Menu.objects.create(name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta)
        )
    role = normal_user.roles.first()
    role.menu.set(menus)
    return next(item for item in menus if item.name == "execute:Dataset")


def _grant_fields(normal_user, registry, menu, names):
    permission = FieldPermission.objects.create(role=normal_user.roles.first(), menu=menu)
    permission.field.set([ModelLabelField.objects.get(name=name, parent=registry) for name in names])
    return permission


def _client_for(user):
    from rest_framework.test import APIClient

    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


class TestRequestFilterFieldPermission:
    def test_hidden_field_filter_rejected(self, user_dataset, normal_user, user_registry, execute_menu):
        """无 nickname 字段权限：请求级 filters 命中隐藏列 → 1001（不给布尔预言机）。"""
        _grant_fields(normal_user, user_registry, execute_menu, ["username"])
        response = _client_for(normal_user).post(
            f"{DATASET_URL}/{user_dataset.pk}/execute",
            {"filters": [{"field": "nickname", "op": "exact", "value": "x"}], "count_only": True},
            format="json",
        )
        body = response.json()
        assert body["code"] == 1001
        # 文案随语言环境（zh/en），断言定位到被拒字段名
        assert "nickname" in str(body["detail"])

    def test_visible_field_filter_allowed(self, user_dataset, normal_user, user_registry, execute_menu):
        _grant_fields(normal_user, user_registry, execute_menu, ["username"])
        normal_user.rules.add(DataPermission.objects.create(name="过滤字段权限-行集", rules=DATA_PERMISSION_ALL_RULES))
        response = _client_for(normal_user).post(
            f"{DATASET_URL}/{user_dataset.pk}/execute",
            {"filters": [{"field": "username", "op": "exact", "value": "nobody"}], "count_only": True},
            format="json",
        )
        assert response.json()["code"] == 1000

    def test_no_field_config_means_no_trim(self, user_dataset, normal_user, execute_menu):
        """浏览者角色没有任何该模型字段权限配置 = 不裁剪（与 execute 列口径一致）。"""
        normal_user.rules.add(
            DataPermission.objects.create(name="过滤字段权限-无配置", rules=DATA_PERMISSION_ALL_RULES)
        )
        response = _client_for(normal_user).post(
            f"{DATASET_URL}/{user_dataset.pk}/execute",
            {"filters": [{"field": "nickname", "op": "exact", "value": "x"}], "count_only": True},
            format="json",
        )
        assert response.json()["code"] == 1000

    def test_aggregate_filters_use_same_scope(self, user_dataset, normal_user, user_registry, execute_menu):
        _grant_fields(normal_user, user_registry, execute_menu, ["username"])
        response = _client_for(normal_user).post(
            f"{DATASET_URL}/{user_dataset.pk}/aggregate",
            {"group_by": "username", "filters": [{"field": "nickname", "op": "exact", "value": "x"}]},
            format="json",
        )
        assert response.json()["code"] == 1001

    def test_superuser_bypasses_field_scope(self, user_dataset, auth_client):
        response = auth_client.post(
            f"{DATASET_URL}/{user_dataset.pk}/execute",
            {"filters": [{"field": "nickname", "op": "exact", "value": "x"}], "count_only": True},
            format="json",
        )
        assert response.json()["code"] == 1000

    def test_filter_count_limit(self, user_dataset, auth_client):
        filters = [{"field": "username", "op": "exact", "value": f"u{index}"} for index in range(FILTER_LIMIT + 1)]
        response = auth_client.post(
            f"{DATASET_URL}/{user_dataset.pk}/execute",
            {"filters": filters, "count_only": True},
            format="json",
        )
        body = response.json()
        assert body["code"] == 1001
        assert str(FILTER_LIMIT) in str(body["detail"])


class TestFilterJsonAnnotation:
    def test_request_filter_json_path_outside_columns(self, json_dataset, json_submissions, superuser):
        """请求级过滤命中 columns 之外的 JSON 路径：按需注解后正常执行（不再 500）。"""
        result = execute_dataset(
            json_dataset,
            superuser,
            extra_filters=[{"field": "data.amount|number", "op": "gte", "value": 5}],
        )
        assert result["total"] == 1
        assert [row["data.kind"] for row in result["rows"]] == ["b"]

    def test_definition_filter_json_path_outside_columns(self, json_dataset, json_submissions, superuser):
        """数据集定义里的同形态脏配置同样可用（存档数据集不再 500）。"""
        json_dataset.filters = [{"field": "data.amount|number", "op": "gte", "value": 5}]
        json_dataset.save(update_fields=["filters"])
        result = execute_dataset(json_dataset, superuser)
        assert result["total"] == 1

    def test_http_request_filter_json_path_outside_columns(self, json_dataset, json_submissions, auth_client):
        response = auth_client.post(
            f"{DATASET_URL}/{json_dataset.pk}/execute",
            {"filters": [{"field": "data.amount|number", "op": "gte", "value": 5}]},
            format="json",
        )
        body = response.json()
        assert body["code"] == 1000, body
        assert body["data"]["total"] == 1

    def test_http_aggregate_filter_json_path_outside_columns(self, json_dataset, json_submissions, auth_client):
        response = auth_client.post(
            f"{DATASET_URL}/{json_dataset.pk}/aggregate",
            {
                "group_by": "data.kind",
                "filters": [{"field": "data.amount|number", "op": "gte", "value": 5}],
            },
            format="json",
        )
        body = response.json()
        assert body["code"] == 1000, body
        assert body["data"]["series"] == [{"name": "b", "raw": "b", "value": 1}]
