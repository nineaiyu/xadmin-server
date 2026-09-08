# -*- coding: utf-8 -*-
"""扩展：角色软删除与回收站（权限缓存失效依赖 post_save 信号）。"""

from unittest.mock import patch

import pytest

import system.signal_handler
from system.models import UserRole

pytestmark = pytest.mark.django_db

ROLE_URL = "/api/system/role"


def _create_role(auth_client, name="测试角色", code="test_role"):
    resp = auth_client.post(ROLE_URL, {"name": name, "code": code, "fields": {}}, format="json")
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestRoleRecycleBin:
    def test_soft_delete_and_restore(self, auth_client):
        pk = _create_role(auth_client)
        with patch.object(system.signal_handler, "batch_invalid_cache") as invalid_mock:
            resp = auth_client.delete(f"{ROLE_URL}/{pk}")
        assert resp.data["code"] == 1000
        # 软删除：默认列表不可见 + post_save 失效权限缓存
        assert not UserRole.objects.filter(pk=pk).exists()
        assert UserRole.all_objects.get(pk=pk).deleted_at is not None
        assert invalid_mock.called

        # 回收站可见并恢复（逐行 save，恢复同样触发 post_save 失效权限缓存）
        resp = auth_client.get(f"{ROLE_URL}/recycle")
        assert any(r["pk"] == pk for r in resp.data["data"]["results"])
        with patch.object(system.signal_handler, "batch_invalid_cache") as restore_mock:
            resp = auth_client.patch(f"{ROLE_URL}/recycle/restore", {"pks": [pk]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert UserRole.objects.filter(pk=pk).exists()
        assert restore_mock.called

    def test_batch_destroy_soft_deletes_with_signal(self, auth_client):
        pk1 = _create_role(auth_client, name="批量A", code="batch_a")
        pk2 = _create_role(auth_client, name="批量B", code="batch_b")
        with patch.object(system.signal_handler, "batch_invalid_cache") as invalid_mock:
            resp = auth_client.post(f"{ROLE_URL}/batch-destroy", [pk1, pk2], format="json")
        assert resp.data["code"] == 1000, resp.data
        # 逐行软删（不走单 SQL update，post_save 信号照常触发）
        for pk in (pk1, pk2):
            assert not UserRole.objects.filter(pk=pk).exists()
            assert UserRole.all_objects.get(pk=pk).deleted_at is not None
        assert invalid_mock.called

    def test_deleted_role_releases_name_and_code(self, auth_client):
        pk = _create_role(auth_client, name="编辑者", code="editor")
        auth_client.delete(f"{ROLE_URL}/{pk}")

        # 已删除角色不再占用 name/code，可创建同名同码新角色
        resp = auth_client.post(ROLE_URL, {"name": "编辑者", "code": "editor", "fields": {}}, format="json")
        assert resp.data["code"] == 1000, resp.data

        # 旧角色恢复时与活跃数据唯一键冲突：跳过该行（可读提示），不产生 500
        resp = auth_client.patch(f"{ROLE_URL}/recycle/restore", {"pks": [pk]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert UserRole.all_objects.filter(pk=pk, deleted_at__isnull=False).exists()

    def test_duplicate_active_code_rejected(self, auth_client):
        _create_role(auth_client, name="角色A", code="dup")
        resp = auth_client.post(ROLE_URL, {"name": "角色B", "code": "dup", "fields": {}}, format="json")
        assert resp.data["code"] != 1000

    def test_purge_removes_row(self, auth_client):
        pk = _create_role(auth_client)
        auth_client.delete(f"{ROLE_URL}/{pk}")
        resp = auth_client.delete(f"{ROLE_URL}/recycle/purge", {"pks": [pk]}, format="json")
        assert resp.data["code"] == 1000
        assert not UserRole.all_objects.filter(pk=pk).exists()
