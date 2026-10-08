# -*- coding: utf-8 -*-
"""内置角色批量删除保护：单删与批删必须同口径拦截。

历史缺陷：``get_queryset`` 仅在 ``action == "destroy"`` 时排除内置角色，
``batch_destroy`` 直接 ``get_queryset()``——单删被拦、批删可把内置角色软删进
回收站（`identity/views/admin/role.py` + `packages/xadmin-common/common/core/modelset/batch.py`）。
"""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from identity.builtin import sync_builtin_roles
from identity.models import UserRole
from identity.views.admin.role import RoleViewSet

pytestmark = pytest.mark.django_db


def _batch_delete(user, pks):
    factory = APIRequestFactory()
    request = factory.post("/api/system/role/batch-destroy", [str(pk) for pk in pks], format="json")
    force_authenticate(request, user=user)
    return RoleViewSet.as_view({"post": "batch_destroy"})(request)


class TestBuiltinRoleBatchDestroy:
    def test_batch_delete_builtin_blocked(self, superuser):
        """批删清单只含内置角色：请求成功但内置角色不被软删。"""
        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")
        response = _batch_delete(superuser, [role.pk])
        assert response.data.get("code") == 1000
        role.refresh_from_db()
        assert role.deleted_at is None
        assert UserRole.all_objects.filter(code="SystemAdmin", deleted_at__isnull=False).exists() is False

    def test_mixed_batch_only_deletes_normal(self, superuser):
        """混批：普通角色正常删除，内置角色跳过（被排除的 pk 不进 success）。"""
        sync_builtin_roles()
        builtin = UserRole.objects.get(code="SystemAdmin")
        normal = UserRole.objects.create(name="业务角色", code="biz_batch")
        response = _batch_delete(superuser, [builtin.pk, normal.pk])
        assert response.data.get("code") == 1000
        success = response.data["data"]["success"]
        assert str(normal.pk) in success
        assert str(builtin.pk) not in success
        assert UserRole.all_objects.filter(pk=normal.pk, deleted_at__isnull=False).exists()
        builtin.refresh_from_db()
        assert builtin.deleted_at is None

    def test_single_delete_still_blocked(self, superuser):
        """回归基线：单删拦截语义不变（批删收口不得反向放松单删）。"""
        sync_builtin_roles()
        role = UserRole.objects.get(code="SystemAdmin")
        factory = APIRequestFactory()
        request = factory.delete(f"/api/system/role/{role.pk}")
        force_authenticate(request, user=superuser)
        response = RoleViewSet.as_view({"delete": "destroy"})(request, pk=role.pk)
        # get_queryset 排除内置角色 → get_object 404（统一响应包装为 400）；
        # 异常处理器会回滚包裹事务，其后不可再查库（与 test_builtin_role 同口径）
        assert response.status_code == 400
