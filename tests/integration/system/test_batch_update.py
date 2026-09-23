# -*- coding: utf-8 -*-
"""通用批量更新与批量响应明细集成测试。

口径钉死：
- 字段白名单（`batch_update_fields`）之外的字段整批拒绝；未声明白名单的视图不可用
  （fail-closed）——批量入口不能成为绕过字段权限的后门；
- 逐项走序列化器校验（与单条同口径）+ 按项隔离事务 → 部分成功语义 + 失败明细；
- 批量删除同步返回 `data.success / data.failures` 明细（detail 文案保持历史口径）。
"""

import pytest

from system.models import UserInfo, UserRole

USER_URL = "/api/system/user/batch-update"
ROLE_URL = "/api/system/role/batch-update"
DEPT_URL = "/api/system/dept/batch-update"
TASK_URL = "/api/system/tasks/periodic/batch-update"
LEAVE_URL = "/api/system/leaves/batch-update"
ROLE_DESTROY_URL = "/api/system/role/batch-destroy"
MISSING_PK = "999999999"

pytestmark = pytest.mark.django_db


class TestBatchPartialUpdate:
    def test_batch_update_user_is_active(self, auth_client, normal_user):
        another = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        resp = auth_client.post(
            USER_URL,
            {"pks": [str(normal_user.pk), str(another.pk)], "fields": {"is_active": False}},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["updated"] == 2
        assert len(data["success"]) == 2
        assert data["failures"] == []
        flags = set(UserInfo.objects.filter(pk__in=[normal_user.pk, another.pk]).values_list("is_active", flat=True))
        assert flags == {False}

    def test_field_outside_whitelist_rejected(self, auth_client, normal_user):
        resp = auth_client.post(
            USER_URL,
            {"pks": [str(normal_user.pk)], "fields": {"username": "hacked"}},
            format="json",
        )
        assert resp.data["code"] == 1004
        normal_user.refresh_from_db()
        assert normal_user.username == "zhangsan"

    def test_unknown_pk_collected_as_failure(self, auth_client, normal_user):
        resp = auth_client.post(
            USER_URL,
            {"pks": [str(normal_user.pk), MISSING_PK], "fields": {"is_active": False}},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["updated"] == 1
        assert [item["pk"] for item in data["failures"]] == [MISSING_PK]
        assert data["failures"][0]["reason"]

    def test_view_without_whitelist_rejected(self, auth_client):
        """未混入批量更新的视图不暴露该端点（405，等价于未开放）"""
        resp = auth_client.post(LEAVE_URL, {"pks": ["x"], "fields": {"status": 1}}, format="json")
        assert resp.status_code == 405

    def test_item_validation_error_reported(self, auth_client, normal_user):
        """逐项校验失败只影响该项（is_active 合法、dept 非法）"""
        resp = auth_client.post(
            USER_URL,
            {"pks": [str(normal_user.pk)], "fields": {"dept": "not-a-uuid"}},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["updated"] == 0
        assert resp.data["data"]["failures"][0]["reason"]

    def test_too_many_items_rejected(self, auth_client):
        resp = auth_client.post(USER_URL, {"pks": ["x"] * 501, "fields": {"is_active": False}}, format="json")
        assert resp.data["code"] == 1004

    def test_role_batch_update(self, auth_client, role):
        resp = auth_client.post(ROLE_URL, {"pks": [str(role.pk)], "fields": {"is_active": False}}, format="json")
        assert resp.data["code"] == 1000, resp.data
        role.refresh_from_db()
        assert role.is_active is False

    def test_dept_batch_update_leader_whitelist(self, auth_client, dept, normal_user):
        resp = auth_client.post(
            DEPT_URL, {"pks": [str(dept.pk)], "fields": {"leader": str(normal_user.pk)}}, format="json"
        )
        assert resp.data["code"] == 1000, resp.data
        dept.refresh_from_db()
        assert dept.leader_id == normal_user.pk

    def test_periodic_task_whitelist_rejects_other_fields(self, auth_client):
        resp = auth_client.post(TASK_URL, {"pks": ["1"], "fields": {"name": "x"}}, format="json")
        assert resp.data["code"] == 1004


class TestBatchDestroyDetail:
    def test_success_and_failures_included(self, auth_client, role):
        other = UserRole.objects.create(name="临时角色", code="tmp_role")
        resp = auth_client.post(ROLE_DESTROY_URL, [str(role.pk), str(other.pk)], format="json")
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert set(data["success"]) == {str(role.pk), str(other.pk)}
        assert data["failures"] == []
