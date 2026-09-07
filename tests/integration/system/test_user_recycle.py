# -*- coding: utf-8 -*-
"""扩展：用户软删除与回收站（登录即时失效、恢复复活、回收站用户名占用、物理清除）。"""
import pytest
from django.contrib.auth import authenticate
from django.test import override_settings

from system.models import UserInfo

pytestmark = pytest.mark.django_db

USER_URL = "/api/system/user"

# 用户删除为敏感操作（需 MFA 二次确认），测试中关闭总开关
MFA_OFF = override_settings(SECURITY_MFA_CONFIRM_ENABLED=False)


class TestUserRecycleBin:
    @MFA_OFF
    def test_soft_delete_blocks_login_and_restore_revives(self, auth_client, normal_user):
        uid = normal_user.pk
        assert authenticate(username="zhangsan", password="Test@123456") == normal_user

        resp = auth_client.delete(f"{USER_URL}/{uid}")
        assert resp.data["code"] == 1000, resp.data

        # 软删除：默认查询不可见，登录（默认管理器）立即失效
        assert not UserInfo.objects.filter(pk=uid).exists()
        assert UserInfo.all_objects.get(pk=uid).deleted_at is not None
        assert authenticate(username="zhangsan", password="Test@123456") is None

        # 回收站可见并恢复，恢复后登录复活
        resp = auth_client.get(f"{USER_URL}/recycle")
        assert any(str(r["pk"]) == str(uid) for r in resp.data["data"]["results"])
        resp = auth_client.patch(f"{USER_URL}/recycle/restore", {"pks": [uid]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert UserInfo.objects.filter(pk=uid).exists()
        assert authenticate(username="zhangsan", password="Test@123456") == UserInfo.objects.get(pk=uid)

    @MFA_OFF
    def test_recycled_username_still_occupied(self, auth_client, superuser):
        """auth.E003 要求 USERNAME_FIELD 全局唯一，回收站用户的用户名不释放。"""
        victim = UserInfo.objects.create_user(username="leaver", password="Test@123456")
        auth_client.delete(f"{USER_URL}/{victim.pk}")
        assert UserInfo.all_objects.get(pk=victim.pk).deleted_at is not None

        resp = auth_client.post(
            USER_URL, {"username": "leaver", "password": "Test@123456"}, format="json"
        )
        assert resp.data["code"] != 1000, resp.data

    @MFA_OFF
    def test_purge_removes_user(self, auth_client, normal_user):
        uid = normal_user.pk
        auth_client.delete(f"{USER_URL}/{uid}")
        resp = auth_client.delete(f"{USER_URL}/recycle/purge", {"pks": [uid]}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not UserInfo.all_objects.filter(pk=uid).exists()

    @MFA_OFF
    def test_batch_destroy_soft_deletes(self, auth_client, superuser):
        u1 = UserInfo.objects.create_user(username="batch-a", password="Test@123456")
        u2 = UserInfo.objects.create_user(username="batch-b", password="Test@123456")
        resp = auth_client.post(f"{USER_URL}/batch-destroy", [str(u1.pk), str(u2.pk)], format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not UserInfo.objects.filter(pk__in=[u1.pk, u2.pk]).exists()
        assert UserInfo.all_objects.filter(pk__in=[u1.pk, u2.pk], deleted_at__isnull=False).count() == 2
