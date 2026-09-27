# -*- coding: utf-8 -*-
"""内置标签：post_migrate 幂等同步 / API 删除保护 / builtin 写路径只读。"""

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from system.builtin import BUILTIN_TAGS, sync_builtin_tags
from system.models import Tag

pytestmark = pytest.mark.django_db


class TestBuiltinTagSync:
    def test_sync_creates_builtin_tags(self):
        """同步后内置标签齐备（测试建库时 post_migrate 已同步过一次，幂等再跑仍齐备）。"""
        sync_builtin_tags()
        for spec in BUILTIN_TAGS:
            tag = Tag.objects.get(name=spec["name"])
            assert tag.builtin is True
            assert tag.color == spec["color"]

    def test_sync_idempotent(self):
        """重复同步幂等：不重复创建、字段无变化时不计变更。"""
        sync_builtin_tags()
        assert sync_builtin_tags() == 0

    def test_sync_preserves_admin_edits(self):
        """管理员改色/改备注是合法改动：同步只补缺，不回写清单值。"""
        sync_builtin_tags()
        tag = Tag.objects.get(name=BUILTIN_TAGS[0]["name"])
        tag.color = "#123456"
        tag.remark = "运营自定义备注"
        tag.save()
        assert sync_builtin_tags() == 0
        tag.refresh_from_db()
        assert tag.color == "#123456"
        assert tag.remark == "运营自定义备注"

    def test_sync_recreates_deleted_tag(self):
        """被误删的内置标签在下次同步补回（Tag 无软删除，直接重建）。"""
        sync_builtin_tags()
        Tag.objects.filter(name=BUILTIN_TAGS[0]["name"]).delete()
        assert sync_builtin_tags() >= 1
        assert Tag.objects.filter(name=BUILTIN_TAGS[0]["name"], builtin=True).exists()

    def test_post_migrate_sync_runs(self):
        """post_migrate 钩子生效：migrate 命令后内置标签存在。"""
        call_command("migrate", run_syncdb=False, verbosity=0)
        names = {spec["name"] for spec in BUILTIN_TAGS}
        assert Tag.objects.filter(builtin=True, name__in=names).count() == len(BUILTIN_TAGS)


class TestBuiltinTagProtection:
    def test_builtin_tag_delete_blocked(self, superuser):
        sync_builtin_tags()
        tag = Tag.objects.get(name=BUILTIN_TAGS[0]["name"])
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        response = client.delete(f"/api/system/tags/{tag.pk}")
        assert response.status_code == 400
        assert Tag.objects.filter(pk=tag.pk).exists()

    def test_builtin_flag_read_only_via_api(self, superuser):
        """API 写路径不可篡改 builtin：create 携带 builtin=true 也落为 False。"""
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        created = client.post("/api/system/tags", {"name": "伪装内置", "builtin": True}, format="json").json()
        assert created["code"] == 1000
        assert created["data"]["builtin"] is False

    def test_normal_tag_delete_allowed(self, superuser):
        sync_builtin_tags()
        tag = Tag.objects.create(name="业务标签")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        response = client.delete(f"/api/system/tags/{tag.pk}")
        assert response.status_code in (200, 204)
        assert not Tag.objects.filter(pk=tag.pk).exists()
