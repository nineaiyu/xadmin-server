# -*- coding: utf-8 -*-
"""影响面预检与引用保护测试。

覆盖：影响面端点（角色 / 数据集 / 批量汇总）、权限与入参校验、
引用保护开关（默认不阻断 / 登记模型要求显式确认 / 批量删除同口径）。
"""

import pytest

from system.models import (
    Dashboard,
    DataDict,
    Dataset,
    DynamicForm,
    Menu,
    Screen,
    UserInfo,
    UserRole,
)

pytestmark = pytest.mark.django_db

ROLE_IMPACT_URL = "/api/system/role/impact"


class TestImpactPreview:
    def test_role_impact_counts_users(self, auth_client):
        role = UserRole.objects.create(name="影响面角色", code="impact_role")
        user = UserInfo.objects.create_user(username="impact_user", password="Test@123456")
        user.roles.add(role)
        body = auth_client.post(ROLE_IMPACT_URL, {"pks": [str(role.pk)]}, format="json").json()
        assert body["code"] == 1000
        data = body["data"]
        assert data["has_impact"] is True
        detail = data["results"][0]
        counts = {item["key"]: item["count"] for item in detail["items"]}
        assert counts["role_users"] == 1
        samples = {item["key"]: item["samples"] for item in detail["items"]}
        assert "impact_user" in samples["role_users"]
        assert data["totals"][0]["count"] >= 1

    def test_zero_impact_role(self, auth_client):
        role = UserRole.objects.create(name="无引用角色", code="impact_role2")
        body = auth_client.post(ROLE_IMPACT_URL, {"pks": [str(role.pk)]}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["has_impact"] is False
        assert body["data"]["results"][0]["items"] == []

    def test_batch_totals(self, auth_client):
        roles = [UserRole.objects.create(name=f"批角色{i}", code=f"impact_batch_{i}") for i in range(2)]
        user = UserInfo.objects.create_user(username="impact_batch_user", password="Test@123456")
        user.roles.add(*roles)
        body = auth_client.post(ROLE_IMPACT_URL, {"pks": [str(role.pk) for role in roles]}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["has_impact"] is True
        total = next(item for item in body["data"]["totals"] if item["key"] == "role_users")
        assert total["count"] == 2

    def test_dataset_impact_cards_and_screens(self, auth_client, superuser):
        dataset = Dataset.objects.create(
            name="影响面数据集", bound_model="system.userinfo", columns=["username"], creator=superuser
        )
        Dashboard.objects.create(
            name="影响面看板", layout=[{"id": "card-1", "dataset": str(dataset.pk)}], creator=superuser
        )
        Screen.objects.create(name="影响面大屏", dashboards=[str(dataset.pk)], creator=superuser)
        body = auth_client.post("/api/system/datasets/impact", {"pks": [str(dataset.pk)]}, format="json").json()
        counts = {item["key"]: item["count"] for item in body["data"]["results"][0]["items"]}
        assert counts["dataset_cards"] == 1
        assert counts["dataset_screens"] == 1

    def test_dict_impact_form_refs(self, auth_client, superuser):
        dict_row = DataDict.objects.create(label="影响面字典", code="impact_dict")
        DynamicForm.objects.create(
            name="引用字典表单",
            schema={"fields": [{"key": "level", "label": "级别", "type": "select", "dict": "impact_dict"}]},
            creator=superuser,
        )
        body = auth_client.post("/api/system/dict/impact", {"pks": [str(dict_row.pk)]}, format="json").json()
        counts = {item["key"]: item["count"] for item in body["data"]["results"][0]["items"]}
        assert counts["dict_form_refs"] == 1

    def test_menu_impact_role_bindings(self, auth_client, menu_factory):
        menu = menu_factory(name="list:ImpactDemo")
        role = UserRole.objects.create(name="菜单角色", code="impact_menu_role")
        role.menu.add(menu)
        body = auth_client.post("/api/system/menu/impact", {"pks": [str(menu.pk)]}, format="json").json()
        counts = {item["key"]: item["count"] for item in body["data"]["results"][0]["items"]}
        assert counts["menu_roles"] == 1
        assert Menu.objects.filter(pk=menu.pk).exists()

    def test_invalid_payload_rejected(self, auth_client):
        assert auth_client.post(ROLE_IMPACT_URL, {}, format="json").json()["code"] == 1004
        assert auth_client.post(ROLE_IMPACT_URL, {"pks": []}, format="json").json()["code"] == 1004

    def test_unsupported_resource_not_available(self, auth_client):
        """未混入影响面 Action 的视图不提供 /impact（404/405；前端探测后静默跳过）。"""
        response = auth_client.post("/api/system/leaves/impact", {"pks": ["1"]}, format="json")
        assert response.status_code in (404, 405)


class TestImpactRegistryAlignment:
    """影响面注册表与视图集混入一致（新增资源须三处同步：CALCULATORS / 混入 / 前端白名单）。"""

    VIEWSETS = (
        ("system.views.admin.role", "RoleViewSet"),
        ("system.views.admin.dept", "DeptViewSet"),
        ("system.views.admin.dict", "DataDictViewSet"),
        ("system.views.dataset", "DatasetViewSet"),
        ("system.views.admin.approval_flow", "ApprovalFlowViewSet"),
        ("system.views.dform", "DynamicFormViewSet"),
        ("system.views.analysis", "ScreenViewSet"),
        ("system.views.admin.menu", "MenuViewSet"),
    )

    def test_viewset_models_match_calculators(self):
        from django.utils.module_loading import import_string

        from common.core.modelset import ImpactPreviewAction
        from system.utils.impact import IMPACT_CALCULATORS

        models = set()
        for module, name in self.VIEWSETS:
            viewset = import_string(f"{module}.{name}")
            assert issubclass(viewset, ImpactPreviewAction), f"{name} 未混入 ImpactPreviewAction"
            models.add(viewset.queryset.model._meta.label_lower)
        assert models == set(IMPACT_CALCULATORS), (
            "影响面视图集与 IMPACT_CALCULATORS 不一致（新增资源需同步计算器 / 混入 / 前端白名单）"
        )


class TestImpactGuard:
    @staticmethod
    def _role_with_user():
        role = UserRole.objects.create(name="保护角色", code="guard_role")
        user = UserInfo.objects.create_user(username="guard_user", password="Test@123456")
        user.roles.add(role)
        return role

    def test_default_no_guard(self, auth_client):
        """默认 IMPACT_GUARD_MODELS 为空：删除行为零变化。"""
        role = self._role_with_user()
        assert auth_client.delete(f"/api/system/role/{role.pk}").json()["code"] == 1000
        assert not UserRole.objects.filter(pk=role.pk).exists()

    def test_guard_requires_confirmation(self, auth_client, settings):
        settings.IMPACT_GUARD_MODELS = ["system.userrole"]
        role = self._role_with_user()
        response = auth_client.delete(f"/api/system/role/{role.pk}")
        assert response.status_code == 400
        assert UserRole.objects.filter(pk=role.pk).exists()

        confirmed = auth_client.delete(f"/api/system/role/{role.pk}?impact_confirmed=true")
        assert confirmed.json()["code"] == 1000
        assert not UserRole.objects.filter(pk=role.pk).exists()

    def test_guard_allows_zero_impact(self, auth_client, settings):
        settings.IMPACT_GUARD_MODELS = ["system.userrole"]
        role = UserRole.objects.create(name="无引用保护角色", code="guard_role2")
        assert auth_client.delete(f"/api/system/role/{role.pk}").json()["code"] == 1000

    def test_batch_destroy_guard(self, auth_client, settings):
        settings.IMPACT_GUARD_MODELS = ["system.userrole"]
        role = self._role_with_user()
        response = auth_client.post("/api/system/role/batch-destroy", [str(role.pk)], format="json")
        assert response.status_code == 400
        assert UserRole.objects.filter(pk=role.pk).exists()

        confirmed = auth_client.post(
            "/api/system/role/batch-destroy?impact_confirmed=true", [str(role.pk)], format="json"
        )
        assert confirmed.json()["code"] == 1000
