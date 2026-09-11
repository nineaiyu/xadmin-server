# -*- coding: utf-8 -*-
"""内置角色：post_migrate 幂等同步 / 删除与改 code 保护 / 回收站恢复语义。"""

import pytest
from django.core.management import call_command
from rest_framework.test import APIRequestFactory, force_authenticate

from system.builtin import BUILTIN_ROLES, sync_builtin_roles
from system.models import UserRole
from system.views.admin.role import RoleViewSet

pytestmark = pytest.mark.django_db


def _viewset(action):
    return RoleViewSet.as_view({"delete": "destroy", "post": "recycle_purge", "put": "update"})


class TestBuiltinRoleSync:
    def test_sync_creates_builtin_roles(self):
        """同步后内置角色齐备（测试建库时 post_migrate 已同步过一次，幂等再跑仍齐备）。"""
        sync_builtin_roles()
        for spec in BUILTIN_ROLES:
            role = UserRole.objects.get(code=spec["code"])
            assert role.builtin is True
            assert role.is_active is True
            assert role.name == spec["name"]

    def test_sync_grants_all_menus_to_admin(self, menu_factory):
        """SystemAdmin 同步时自动挂全部活跃菜单；停用菜单不授予。"""
        from system.models import Menu

        menu_factory("菜单A", path="api/a$", method="GET", menu_type=Menu.MenuChoices.MENU)
        sync_builtin_roles()
        admin = UserRole.objects.get(code="SystemAdmin")
        assert admin.menu.count() == 1

    def test_sync_idempotent(self):
        """重复同步幂等：不重复创建、无字段变更时不计变更。"""
        sync_builtin_roles()
        before = list(UserRole.objects.filter(builtin=True).values_list("code", flat=True))
        changed = sync_builtin_roles()
        assert changed == 0
        after = list(UserRole.objects.filter(builtin=True).values_list("code", flat=True))
        assert before == after

    def test_sync_restores_recycled_role(self):
        """回收站中的同名角色被同步恢复（内置角色不停留在已删除态）。"""
        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")
        role.delete()  # 软删进回收站
        assert UserRole.objects.filter(code="SystemAdmin").exists() is False
        changed = sync_builtin_roles()
        assert changed >= 1
        role.refresh_from_db()
        assert role.deleted_at is None
        assert role.builtin is True

    def test_post_migrate_sync_runs(self):
        """post_migrate 钩子生效：migrate 命令后内置角色存在（同步幂等不重复）。"""
        call_command("migrate", run_syncdb=False, verbosity=0)
        assert UserRole.objects.filter(code="SystemAdmin", builtin=True).exists()


class TestBuiltinRoleProtection:
    def _delete(self, user, pk):
        factory = APIRequestFactory()
        request = factory.delete(f"/api/system/role/{pk}")
        force_authenticate(request, user=user)
        return _viewset("destroy")(request, pk=pk)

    def test_builtin_role_delete_blocked(self, superuser):
        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")
        response = self._delete(superuser, role.pk)
        # get_queryset 排除内置角色 → get_object 404（xadmin 统一响应包装为 400）
        assert response.status_code == 400

    def test_normal_role_delete_allowed(self, superuser):
        sync_builtin_roles()
        role = UserRole.objects.create(name="业务角色", code="biz")
        response = self._delete(superuser, role.pk)
        assert response.status_code in (200, 204)

    def test_recycle_purge_blocked(self, superuser):
        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")
        role.delete()  # 先软删进回收站
        factory = APIRequestFactory()
        request = factory.post("/api/system/role/recycle/purge", [str(role.pk)], format="json")
        force_authenticate(request, user=superuser)
        _viewset("recycle_purge")(request)
        assert UserRole.all_objects.filter(code="SystemAdmin").exists() is True

    def test_builtin_code_change_blocked(self, superuser, api_client):
        from system.serializers.role import RoleSerializer

        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")

        # 序列化器层：validate 拦截 code 变更
        serializer = RoleSerializer(
            instance=role,
            data={"name": role.name, "code": "Hacked", "is_active": True, "fields": {}},
        )
        assert not serializer.is_valid()
        assert "code" in serializer.errors

        # API 层：PUT 400 且 code 未变
        api_client.force_authenticate(user=superuser)
        resp = api_client.put(
            f"/api/system/role/{role.pk}",
            {"name": role.name, "code": "Hacked", "is_active": True, "fields": {}},
            format="json",
        )
        assert resp.status_code == 400
        role.refresh_from_db()
        assert role.code == "SystemAdmin"

    def test_normal_role_cannot_occupy_builtin_code(self, superuser, api_client):
        sync_builtin_roles()
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(
            "/api/system/role",
            {"name": "伪装管理员", "code": "SystemAdmin", "fields": {}},
            format="json",
        )
        assert resp.status_code == 400
