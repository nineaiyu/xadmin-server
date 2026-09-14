# -*- coding: utf-8 -*-
"""敏感操作审批挂载点：角色/部门删除的可配置审批拦截。

两条纪律：
1. 默认 APPROVAL_REQUIRED_PATHS 为空 = 完全不拦截（渐进启用，不改变既有行为）；
2. 配置命中后走 412 + code 1002 协议——建 PENDING 单、业务代码不执行，
   审批通过后由申请人携令牌重发。
"""

import pytest

from common.core.config import SysConfig
from system.models import DeptInfo, UserInfo, UserRole
from system.models.approval import ApprovalRequest

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver(db):
    """审批人（在用的其他超管；申请人不能自审，故必须另有其人）。"""
    return UserInfo.objects.create_superuser(
        username="sensitive_approver", email="a@example.com", password="Test@123456"
    )


@pytest.fixture
def target_role(db):
    return UserRole.objects.create(name="待删角色", code="to_be_removed")


def enable(paths):
    SysConfig.set_value("APPROVAL_REQUIRED_PATHS", paths)


class TestSensitiveApprovalMounts:
    def test_default_config_does_not_intercept(self, auth_client, target_role):
        """默认休眠：删除直接执行（保证既有部署与自动化不受影响）。"""
        response = auth_client.delete(f"/api/system/role/{target_role.pk}")
        assert response.data["code"] == 1000
        assert not UserRole.objects.filter(pk=target_role.pk).exists()

    def test_role_destroy_requires_approval(self, auth_client, approver, target_role):
        enable([r"^/api/system/role/"])
        response = auth_client.delete(f"/api/system/role/{target_role.pk}")
        assert response.status_code == 412
        assert response.data["code"] == 1002
        assert response.data["type"] == "approval_required"
        # 业务未执行，留下待审批单（申请人 = 发起删除的超管）
        assert UserRole.objects.filter(pk=target_role.pk).exists()
        approval = ApprovalRequest.objects.get(status=ApprovalRequest.Status.PENDING)
        assert approval.method == "DELETE"
        assert approval.path.endswith(str(target_role.pk))

    def test_role_batch_destroy_requires_approval(self, auth_client, approver, target_role):
        enable([r"^/api/system/role/"])
        response = auth_client.post("/api/system/role/batch-destroy", [str(target_role.pk)], format="json")
        assert response.status_code == 412
        assert UserRole.objects.filter(pk=target_role.pk).exists()

    def test_dept_destroy_requires_approval(self, auth_client, approver, dept):
        enable([r"^/api/system/dept/"])
        response = auth_client.delete(f"/api/system/dept/{dept.pk}")
        assert response.status_code == 412
        assert DeptInfo.objects.filter(pk=dept.pk).exists()
        assert ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).count() == 1
