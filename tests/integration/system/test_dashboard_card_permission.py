# -*- coding: utf-8 -*-
"""仪表盘卡片级权限（layout[].allowed_roles）集成测试。

一期：授权面（allowed_roles）+ 读取侧按浏览者角色过滤；
二期：字段权限叠加到执行/聚合输出列 + 越权矩阵补强（ADR-042）。
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from system.models import DataPermission, FieldPermission, ModelLabelField, UserInfo, UserRole
from system.models.dataset import Dashboard, Dataset
from system.utils.dataset import aggregate_dataset, execute_dataset, filter_layout_for_user

pytestmark = pytest.mark.django_db

DASHBOARDS_URL = "/api/system/dashboards"

# 行级数据权限：全部数据（value.all）——数据权限默认拒绝，无授权的用户行集为 none()
DATA_PERMISSION_ALL_RULES = [
    {"table": "system.userinfo", "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}
]


@pytest.fixture
def model_registry(db):
    """字段注册表：system.userinfo 的 DATA 白名单节点（模型白名单源，与 test_dataset_api 同款）。"""
    root, _ = ModelLabelField.objects.get_or_create(
        name="system.userinfo",
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "phone", "nickname"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def dataset(model_registry):
    return Dataset.objects.create(name="card_ds", bound_model="system.userinfo", visibility="shared")


def card(card_id, dataset, roles=None):
    item = {"id": card_id, "dataset": str(dataset.pk), "title": card_id, "chart_type": "number"}
    if roles is not None:
        item["allowed_roles"] = roles
    return item


class TestFilterLayout:
    def test_card_without_roles_visible_to_all(self, dataset, normal_user):
        layout = [card("c1", dataset, [])]
        assert filter_layout_for_user(layout, normal_user) == layout

    def test_card_filtered_by_role(self, dataset, normal_user):
        layout = [card("c1", dataset, ["card_role"]), card("c2", dataset, ["other_role"])]
        # 无任何命中角色 → 两张卡片都不可见（fail-closed）
        assert filter_layout_for_user(layout, normal_user) == []
        role = UserRole.objects.create(name="CARD", code="card_role", is_active=True)
        normal_user.roles.add(role)
        assert [item["id"] for item in filter_layout_for_user(layout, normal_user)] == ["c1"]

    def test_superuser_sees_all(self, dataset):
        admin = UserInfo.objects.create_superuser(
            username="card_admin", email="card_admin@example.com", password="Test@123456"
        )
        layout = [card("c1", dataset, ["some_role"]), card("c2", dataset, ["another_role"])]
        assert len(filter_layout_for_user(layout, admin)) == 2


class TestCardRoleValidation:
    def test_api_rejects_unknown_role_code(self, auth_client, dataset):
        resp = auth_client.post(
            DASHBOARDS_URL,
            {
                "name": f"卡片校验-{timezone.now().timestamp()}",
                "visibility": "personal",
                "layout": [card("c1", dataset, ["not_exist_role"])],
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_api_accepts_known_role_code(self, auth_client, dataset):
        UserRole.objects.create(name="CARD2", code="card_role2", is_active=True)
        resp = auth_client.post(
            DASHBOARDS_URL,
            {
                "name": f"卡片校验-{timezone.now().timestamp()}",
                "visibility": "personal",
                "layout": [card("c1", dataset, ["card_role2"])],
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data


class TestFieldPermissionOverlay:
    """字段权限叠加到执行/聚合输出列（ADR-042 二期）。

    口径：输出列 = 数据集 columns ∩ 浏览者字段白名单（跨菜单并集）；
    超管全量；浏览者角色无任何字段权限配置 = 全量（字段权限是显式授权行为，
    数据集执行无菜单上下文，fail-closed 全裁会让所有普通用户仪表盘被裁空）。
    """

    @pytest.fixture
    def wide_dataset(self, dataset):
        dataset.columns = ["username", "phone", "nickname"]
        dataset.save(update_fields=["columns"])
        return dataset

    def grant_field_whitelist(self, user, menu_factory, fields):
        """给用户直挂角色配置字段白名单（ROLE 树节点 + FieldPermission；菜单任意）。"""
        menu = menu_factory(name="fp-menu", path="api/system/user$", method="GET")
        parent = ModelLabelField.objects.create(
            name="system.userinfo", label="system.userinfo", field_type=ModelLabelField.FieldChoices.ROLE
        )
        children = [
            ModelLabelField.objects.create(name=f, label=f, parent=parent, field_type=ModelLabelField.FieldChoices.ROLE)
            for f in fields
        ]
        fp = FieldPermission.objects.create(role=user.roles.first(), menu=menu)
        fp.field.add(*children)
        return fp

    def test_execute_trims_columns_to_whitelist(self, wide_dataset, normal_user, menu_factory):
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        result = execute_dataset(wide_dataset, normal_user)
        assert result["columns"] == ["username"]

    def test_execute_row_keys_trimmed_with_row_access(self, wide_dataset, normal_user, menu_factory):
        """有行级数据权限（全部数据）时，行内只含白名单列。"""
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        dp = DataPermission.objects.create(name="全部用户数据", rules=DATA_PERMISSION_ALL_RULES)
        normal_user.rules.add(dp)
        UserInfo.objects.create_user(username="overlay_target", password="Test@123456", nickname="被统计者")
        result = execute_dataset(wide_dataset, normal_user)
        assert result["columns"] == ["username"]
        assert result["total"] >= 1
        assert all(set(row.keys()) == {"username"} for row in result["rows"])

    def test_execute_full_without_field_config(self, wide_dataset, normal_user):
        result = execute_dataset(wide_dataset, normal_user)
        assert result["columns"] == ["username", "phone", "nickname"]

    def test_execute_superuser_full(self, wide_dataset, superuser):
        result = execute_dataset(wide_dataset, superuser)
        assert result["columns"] == ["username", "phone", "nickname"]

    def test_execute_empty_intersection_returns_empty_result(self, wide_dataset, normal_user, menu_factory):
        # 白名单（email）与数据集列（username/phone/nickname）无交集 → 空结果
        self.grant_field_whitelist(normal_user, menu_factory, ["email"])
        result = execute_dataset(wide_dataset, normal_user)
        assert result == {"columns": [], "rows": [], "total": 0, "limit": wide_dataset.row_limit}

    def test_execute_ignores_inactive_role_whitelist(self, wide_dataset, normal_user, menu_factory):
        """字段白名单挂在停用角色上 = 无字段配置（全量），与字段权限装载口径一致。"""
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        normal_user.roles.update(is_active=False)
        result = execute_dataset(wide_dataset, normal_user)
        assert result["columns"] == ["username", "phone", "nickname"]

    def test_aggregate_rejects_hidden_group_by(self, wide_dataset, normal_user, menu_factory):
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        with pytest.raises(ValidationError):
            aggregate_dataset(wide_dataset, normal_user, group_by="phone")

    def test_aggregate_rejects_hidden_value_field(self, wide_dataset, normal_user, menu_factory):
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        with pytest.raises(ValidationError):
            aggregate_dataset(wide_dataset, normal_user, group_by="username", metric="sum", value_field="phone")

    def test_aggregate_allows_visible_fields(self, wide_dataset, normal_user, menu_factory):
        self.grant_field_whitelist(normal_user, menu_factory, ["username"])
        dp = DataPermission.objects.create(name="全部用户数据-聚合", rules=DATA_PERMISSION_ALL_RULES)
        normal_user.rules.add(dp)
        UserInfo.objects.create_user(username="overlay_target2", password="Test@123456", nickname="被统计者")
        result = aggregate_dataset(wide_dataset, normal_user, group_by="username")
        assert result["name"] == "username"
        assert result["metric"] == "count"
        assert any(item["name"] == "overlay_target2" for item in result["series"])


class TestEscalationMatrixPhase2:
    """越权矩阵补强（ADR-042 二期）：跨角色浏览 / 空角色 / 超管旁路 / 卡片级与行级叠加。"""

    @pytest.fixture
    def shared_dashboard(self, dataset):
        return Dashboard.objects.create(
            name="matrix_dashboard",
            visibility=Dashboard.Visibility.SHARED,
            layout=[
                card("c1", dataset, ["role_a"]),
                card("c2", dataset, ["role_b"]),
                card("c3", dataset, []),
            ],
        )

    @staticmethod
    def make_user_with_role(username, code):
        role = UserRole.objects.create(name=code, code=code, is_active=True)
        user = UserInfo.objects.create_user(username=username, password="Test@123456")
        user.roles.add(role)
        return user, role

    def test_cross_role_view_api(self, api_client, shared_dashboard, menu_factory):
        """同一仪表盘：role_a 只见 c1/c3，role_b 只见 c2/c3（跨角色不可互相越看）。"""
        menu = menu_factory(name="dash-list", path="api/system/dashboards$", method="GET")
        user_a, role_a = self.make_user_with_role("matrix_a", "role_a")
        user_b, role_b = self.make_user_with_role("matrix_b", "role_b")
        role_a.menu.add(menu)
        role_b.menu.add(menu)

        api_client.force_authenticate(user=user_a)
        resp_a = api_client.get(DASHBOARDS_URL)
        assert resp_a.data["code"] == 1000, resp_a.data
        layout_a = next(item for item in resp_a.data["data"]["results"] if item["pk"] == str(shared_dashboard.pk))[
            "layout"
        ]
        assert [c["id"] for c in layout_a] == ["c1", "c3"]

        api_client.force_authenticate(user=user_b)
        resp_b = api_client.get(DASHBOARDS_URL)
        layout_b = next(item for item in resp_b.data["data"]["results"] if item["pk"] == str(shared_dashboard.pk))[
            "layout"
        ]
        assert [c["id"] for c in layout_b] == ["c2", "c3"]

    def test_user_without_roles_sees_only_unrestricted_cards(self, shared_dashboard):
        user = UserInfo.objects.create_user(username="matrix_none", password="Test@123456")
        layout = filter_layout_for_user(shared_dashboard.layout, user)
        assert [c["id"] for c in layout] == ["c3"]

    def test_superuser_api_sees_all(self, auth_client, shared_dashboard):
        resp = auth_client.get(f"{DASHBOARDS_URL}/{shared_dashboard.pk}")
        assert resp.data["code"] == 1000, resp.data
        assert [c["id"] for c in resp.data["data"]["layout"]] == ["c1", "c2", "c3"]

    def test_card_visible_rows_still_fail_closed(self, shared_dashboard, dataset, normal_user):
        """卡片级可见（角色命中 c1）≠ 行级数据可见：行级数据权限无授权时执行返回空（兜底）。"""
        role = normal_user.roles.first()
        role.code = "role_a"
        role.save(update_fields=["code"])
        dataset.columns = ["username"]
        dataset.save(update_fields=["columns"])
        layout = filter_layout_for_user(shared_dashboard.layout, normal_user)
        assert [c["id"] for c in layout] == ["c1", "c3"]
        result = execute_dataset(dataset, normal_user)
        assert result["rows"] == []
        assert result["total"] == 0
