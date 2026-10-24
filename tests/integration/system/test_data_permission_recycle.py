# -*- coding: utf-8 -*-
"""数据权限删除治理：软删 / 回收站恢复 / 物理清除 / 影响面预览。"""

import pytest

from system.models import DataPermission

pytestmark = pytest.mark.django_db

BASE = "/api/system/permission"


def _create(superuser, name="E2E 数据规则"):
    return DataPermission.objects.create(name=name, rules=[], creator=superuser, modifier=superuser)


def test_delete_moves_to_recycle_bin_and_restore(auth_client, superuser):
    perm = _create(superuser)

    deleted = auth_client.delete(f"{BASE}/{perm.pk}")
    assert deleted.status_code in (200, 204), deleted.content

    perm.refresh_from_db()
    assert perm.deleted_at is not None
    # 默认管理器过滤软删行，列表不再可见
    assert not DataPermission.objects.filter(pk=perm.pk).exists()

    recycled = auth_client.get(f"{BASE}/recycle")
    assert recycled.status_code == 200
    names = [row["name"] for row in recycled.json()["data"]["results"]]
    assert "E2E 数据规则" in names

    restored = auth_client.patch(f"{BASE}/recycle/restore", {"pks": [str(perm.pk)]}, format="json")
    assert restored.status_code == 200, restored.content
    perm.refresh_from_db()
    assert perm.deleted_at is None
    assert DataPermission.objects.filter(pk=perm.pk).exists()


def test_purge_removes_row_physically(auth_client, superuser):
    perm = _create(superuser, "E2E 待清除规则")
    auth_client.delete(f"{BASE}/{perm.pk}")

    purged = auth_client.delete(f"{BASE}/recycle/purge", {"pks": [str(perm.pk)]}, format="json")
    assert purged.status_code == 200, purged.content
    assert not DataPermission.objects.filter(pk=perm.pk).exists()
    assert not DataPermission.all_objects.filter(pk=perm.pk).exists()


def test_impact_preview_counts_bound_users(auth_client, superuser):
    perm = _create(superuser, "E2E 影响面规则")
    superuser.rules.add(perm)

    response = auth_client.post(f"{BASE}/impact", {"pks": [str(perm.pk)]}, format="json")

    assert response.status_code == 200, response.content
    data = response.json()["data"]["results"][0]
    assert data["has_impact"] is True
    keys = {item["key"] for item in data["items"]}
    assert "dp_users" in keys
