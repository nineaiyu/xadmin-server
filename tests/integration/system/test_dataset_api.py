# -*- coding: utf-8 -*-
"""数据集与仪表盘集成测试。

覆盖：白名单（越权模型/字段/op 拒绝）、fail-closed（无授权空结果、
value.user.id 规则只见本人）、共享语义（personal 不可见 / shared 只读、
非创建者改布局被拒）、越权（匿名/普通用户）、聚合（trend 与分布、桶上限内）。
"""

import pytest
from django.conf import settings as dj_settings

from system.models import DataPermission, Dataset, ModelLabelField, UserInfo, UserRole
from system.models.dataset import Dashboard

pytestmark = pytest.mark.django_db

DATASET_URL = "/api/system/datasets"
DASHBOARD_URL = "/api/system/dashboards"


@pytest.fixture(autouse=True)
def _data_permission_on(settings):
    dj_settings.PERMISSION_DATA_ENABLED = True


@pytest.fixture
def model_registry(db):
    """字段注册表：system.userinfo 三个字段（白名单源）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "email", "is_active"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def dataset(model_registry, superuser):
    return Dataset.objects.create(
        name="用户清单",
        bound_model="system.userinfo",
        columns=["username", "nickname", "email"],
        filters=[],
        row_limit=1000,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def grant_dataset_menus(normal_user):
    """普通用户的角色授予数据集/仪表盘权限菜单（自定义 API 非超管必须有菜单节点）。"""
    from system.models import Menu, MenuMeta

    def _make(name, path, method):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/system/datasets/(?P<pk>[^/.]+)"
    dash_detail = "api/system/dashboards/(?P<pk>[^/.]+)"
    menus = [
        _make("list:Dataset", "api/system/datasets$", "GET"),
        _make("create:Dataset", "api/system/datasets$", "POST"),
        _make("retrieve:Dataset", detail + "$", "GET"),
        _make("partialUpdate:Dataset", detail + "$", "PATCH"),
        _make("update:Dataset", detail + "$", "PUT"),
        _make("destroy:Dataset", detail + "$", "DELETE"),
        _make("execute:Dataset", detail + "/execute$", "POST"),
        _make("aggregate:Dataset", detail + "/aggregate$", "POST"),
        _make("list:DataDashboard", "api/system/dashboards$", "GET"),
        _make("create:DataDashboard", "api/system/dashboards$", "POST"),
        _make("partialUpdate:DataDashboard", dash_detail, "PATCH"),
    ]

    def _grant(user):
        role = user.roles.first() or UserRole.objects.create(name=f"role-{user.username}", code=user.username)
        user.roles.add(role)
        role.menu.set(menus)
        return role

    _grant(normal_user)
    return _grant


def make_permission(user, table="system.userinfo", field="id", value_type="value.user.id", value=""):
    dp = DataPermission.objects.create(
        name=f"dp-{user.username}-{field}",
        rules=[
            {"table": table, "field": field, "type": value_type, "match": "exact", "value": value, "exclude": False}
        ],
    )
    user.rules.add(dp)
    return dp


class TestDatasetCrud:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(DATASET_URL).status_code == 401

    def test_create_with_whitelist(self, auth_client, model_registry):
        payload = {
            "name": "活跃用户",
            "bound_model": "system.userinfo",
            "columns": ["username", "is_active"],
            "filters": [{"field": "is_active", "op": "exact", "value": True}],
            "visibility": "personal",
        }
        response = auth_client.post(DATASET_URL, payload, format="json")
        assert response.status_code == 200, response.data
        assert response.json()["code"] == 1000
        dataset = Dataset.objects.get(name="活跃用户")
        assert dataset.creator.username == "admin"

    def test_create_rejects_out_of_whitelist_model(self, auth_client, model_registry):
        payload = {"name": "坏模型", "bound_model": "demo.book", "columns": ["id"]}
        response = auth_client.post(DATASET_URL, payload, format="json")
        assert response.status_code == 400

    def test_create_rejects_out_of_whitelist_field(self, auth_client, model_registry):
        payload = {"name": "坏字段", "bound_model": "system.userinfo", "columns": ["password"]}
        response = auth_client.post(DATASET_URL, payload, format="json")
        assert response.status_code == 400

    def test_create_rejects_bad_op(self, auth_client, model_registry):
        payload = {
            "name": "坏op",
            "bound_model": "system.userinfo",
            "columns": ["username"],
            "filters": [{"field": "username", "op": "regex", "value": ".*"}],
        }
        response = auth_client.post(DATASET_URL, payload, format="json")
        assert response.status_code == 400

    def test_row_limit_capped(self, auth_client, model_registry):
        payload = {"name": "超大", "bound_model": "system.userinfo", "columns": ["username"], "row_limit": 99999}
        response = auth_client.post(DATASET_URL, payload, format="json")
        assert response.status_code == 400

    def test_meta_lists_registry(self, auth_client, model_registry):
        body = auth_client.get(f"{DATASET_URL}/meta").json()
        assert "system.userinfo" in body["data"]["models"]
        assert "username" in body["data"]["fields"]["system.userinfo"]


class TestDatasetExecute:
    def test_fail_closed_without_grant(self, dataset, normal_user, grant_dataset_menus):
        """核心验收：无任何数据权限授权的用户执行 → 空结果（fail-closed）。"""
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        # 模拟菜单上下文：数据集执行接口自身的菜单（测试环境无菜单则跳过上下文）
        body = client.post(f"{DATASET_URL}/{dataset.pk}/execute", {}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["rows"] == []
        assert body["data"]["total"] == 0

    def test_user_id_grant_sees_only_self(self, auth_client, dataset, normal_user, superuser, grant_dataset_menus):
        """value.user.id 规则：普通用户执行 → 只见本人行。"""
        make_permission(normal_user)
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        body = client.post(f"{DATASET_URL}/{dataset.pk}/execute", {}, format="json").json()
        usernames = [row["username"] for row in body["data"]["rows"]]
        assert usernames == ["zhangsan"]

    def test_superuser_sees_all(self, auth_client, dataset):
        body = auth_client.post(f"{DATASET_URL}/{dataset.pk}/execute", {}, format="json").json()
        assert body["data"]["total"] >= 1

    def test_filters_applied(self, auth_client, dataset):
        dataset.filters = [{"field": "username", "op": "exact", "value": "admin"}]
        dataset.save()
        body = auth_client.post(f"{DATASET_URL}/{dataset.pk}/execute", {}, format="json").json()
        assert [row["username"] for row in body["data"]["rows"]] == ["admin"]

    def test_aggregate_trend(self, auth_client, dataset, model_registry):
        root = ModelLabelField.objects.get(name="system.userinfo")
        ModelLabelField.objects.get_or_create(
            name="created_time",
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "创建时间"},
        )
        dataset.config = {"date_field": "created_time"}
        dataset.save()
        body = auth_client.post(
            f"{DATASET_URL}/{dataset.pk}/aggregate",
            {"group_by": "created_time", "metric": "count", "date_trunc": "day"},
            format="json",
        ).json()
        assert body["code"] == 1000
        assert isinstance(body["data"]["series"], list)
        assert sum(item["value"] for item in body["data"]["series"]) >= 1

    def test_aggregate_trend_groups_by_bucket(self, auth_client, dataset, model_registry):
        """趋势聚合必须按时间桶分组：同桶多行不能裂开（annotate/values 顺序回归守护）。"""
        from django.utils import timezone

        root = ModelLabelField.objects.get(name="system.userinfo")
        ModelLabelField.objects.get_or_create(
            name="date_joined",
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "加入时间"},
        )
        UserInfo.objects.create_user(username="zhaoliu", password="Test@123456")
        # 全部用户 date_joined 压到同一时刻：聚合正确时只允许一个桶
        fixed = timezone.now().replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        UserInfo.objects.update(date_joined=fixed)
        body = auth_client.post(
            f"{DATASET_URL}/{dataset.pk}/aggregate",
            {"group_by": "date_joined", "metric": "count", "date_trunc": "month"},
            format="json",
        ).json()
        assert body["code"] == 1000
        series = body["data"]["series"]
        total_users = UserInfo.objects.count()
        assert sum(item["value"] for item in series) == total_users
        assert len(series) == 1, f"同一时刻的行必须聚进一个桶，实际裂成 {len(series)} 桶"
        assert series[0]["name"] == fixed.strftime("%Y-%m")
        assert series[0]["value"] == total_users

    def test_aggregate_group_label_keeps_falsy_values(self, auth_client, dataset, model_registry):
        """falsy 分组值（is_active=False）是合法分组：桶名 "False"，不得落空串。"""
        UserInfo.objects.update(is_active=False)
        body = auth_client.post(
            f"{DATASET_URL}/{dataset.pk}/aggregate",
            {"group_by": "is_active", "metric": "count"},
            format="json",
        ).json()
        assert body["code"] == 1000
        series = body["data"]["series"]
        assert len(series) == 1
        assert series[0]["name"] == "False"
        assert series[0]["value"] == UserInfo.objects.count()

    def test_aggregate_rejects_non_numeric_sum(self, auth_client, dataset):
        body = auth_client.post(
            f"{DATASET_URL}/{dataset.pk}/aggregate",
            {"group_by": "username", "metric": "sum", "value_field": "username"},
            format="json",
        ).json()
        assert body["code"] == 1001


class TestDatasetVisibility:
    def test_personal_hidden_from_others(self, normal_user, model_registry, grant_dataset_menus):
        """personal 仅创建者可见（superuser 绕过可见性是既定语义，用第二个普通用户断言）。"""
        from rest_framework.test import APIClient

        other = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        grant_dataset_menus(other)
        dataset = Dataset.objects.create(
            name="私人数据集", bound_model="system.userinfo", columns=["username"], creator=normal_user
        )
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=other)
        body = client.get(DATASET_URL).json()
        pks = [item["pk"] for item in body["data"]["results"]]
        assert str(dataset.pk) not in pks

    def test_shared_visible_but_readonly(self, auth_client, normal_user, dataset, grant_dataset_menus):
        """shared：他人可见；修改/删除被拒。"""
        body = auth_client.get(DATASET_URL).json()
        pks = [item["pk"] for item in body["data"]["results"]]
        assert str(dataset.pk) in pks

        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.patch(f"{DATASET_URL}/{dataset.pk}", {"name": "改"}, format="json")
        assert response.status_code == 200
        assert response.json()["code"] == 1003  # 共享只读

    def test_creator_can_update(self, normal_user, model_registry, grant_dataset_menus):
        dataset = Dataset.objects.create(
            name="我的数据集", bound_model="system.userinfo", columns=["username"], creator=normal_user
        )
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.patch(f"{DATASET_URL}/{dataset.pk}", {"description": "自用"}, format="json")
        assert response.status_code == 200
        assert response.json()["code"] == 1000


class TestDashboard:
    def test_crud_and_visibility(self, auth_client, normal_user, dataset, grant_dataset_menus):
        payload = {
            "name": "运营看板",
            "visibility": "shared",
            "layout": [
                {
                    "id": "card-1",
                    "dataset": str(dataset.pk),
                    "title": "用户总数",
                    "chart_type": "number",
                    "span": 6,
                }
            ],
        }
        response = auth_client.post(DASHBOARD_URL, payload, format="json")
        assert response.status_code == 200, response.data
        assert response.json()["code"] == 1000
        dashboard = Dashboard.objects.get(name="运营看板")
        assert dashboard.creator.username == "admin"

        # personal 不可见 / shared 可见
        Dashboard.objects.create(name="私人看板", creator=normal_user, visibility="personal")
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        visible = {item["name"] for item in client.get(DASHBOARD_URL).json()["data"]["results"]}
        assert "运营看板" in visible and "私人看板" in visible
        # superuser 绕过可见性是既定语义；personal 对其他普通用户不可见
        other = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        grant_dataset_menus(other)
        other_client = APIClient(HTTP_USER_AGENT="pytest-agent")
        other_client.force_authenticate(user=other)
        other_view = {item["name"] for item in other_client.get(DASHBOARD_URL).json()["data"]["results"]}
        assert "运营看板" in other_view and "私人看板" not in other_view

        # 共享只读：非创建者改布局被拒
        response = client.patch(f"{DASHBOARD_URL}/{dashboard.pk}", {"name": "改"}, format="json")
        assert response.json()["code"] == 1003

    def test_layout_rejects_unknown_dataset(self, auth_client):
        payload = {"name": "坏卡片", "layout": [{"id": "c1", "dataset": "00000000-0000-0000-0000-000000000000"}]}
        response = auth_client.post(DASHBOARD_URL, payload, format="json")
        assert response.status_code == 400

    def test_layout_rejects_bad_chart_type(self, auth_client, dataset):
        payload = {
            "name": "坏图表",
            "layout": [{"id": "c1", "dataset": str(dataset.pk), "chart_type": "3d"}],
        }
        response = auth_client.post(DASHBOARD_URL, payload, format="json")
        assert response.status_code == 400
