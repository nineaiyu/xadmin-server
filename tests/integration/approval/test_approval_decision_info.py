# -*- coding: utf-8 -*-
"""审批决策信息完备：目标对象快照 / 关联业务对象卡片 / 处理人显示名快照。

- 敏感操作审批：建单落 target_snapshot（对象身份 + 变更前后对照），详情 API 带出；
- 审批流实例：related_object 白名单渲染（leave / dform_submission），业务行删除后降级；
- 处理人快照：流程任务 assignee_display / actor_display、轻量单 approver_display。
"""

import types

import pytest

from approval.models import (
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalInstance,
    ApprovalNodeTask,
    ApprovalRequest,
)
from common.core.config import SysConfig
from system.models import UserInfo, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver(db):
    """审批人（在用的其他超管；申请人不能自审）。"""
    return UserInfo.objects.create_superuser(
        username="u1_approver", email="u1@example.com", password="Test@123456", nickname="审批人甲"
    )


class TestTargetSnapshot:
    def test_delete_snapshot_records_object_identity(self, auth_client, approver):
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"^/api/system/role/"])
        role = UserRole.objects.create(name="待删角色", code="u1_role")
        response = auth_client.delete(f"/api/system/role/{role.pk}")
        assert response.status_code == 412
        approval = ApprovalRequest.objects.get(status=ApprovalRequest.Status.PENDING)
        snapshot = approval.target_snapshot
        assert snapshot["model"] == "system.userrole"
        assert snapshot["pk"] == str(role.pk)
        assert snapshot["name"].startswith("待删角色")

    def test_detail_api_returns_snapshot(self, auth_client, approver):
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"^/api/system/role/"])
        role = UserRole.objects.create(name="详情角色", code="u1_role2")
        auth_client.delete(f"/api/system/role/{role.pk}")
        approval = ApprovalRequest.objects.get(status=ApprovalRequest.Status.PENDING)
        body = auth_client.get(f"/api/system/approvals/{approval.pk}").json()["data"]
        assert body["target_snapshot"]["pk"] == str(role.pk)

    def test_patch_style_changes_diff_rendered(self):
        """变更类请求（PATCH body）→ 快照含「变更前 → 变更后」对照。"""
        from approval.utils.approval.snapshot import build_target_snapshot
        from system.views.admin.role import RoleViewSet

        role = UserRole.objects.create(name="原角色名", code="u1_role3")
        view = RoleViewSet()
        view.action = "update"
        view.kwargs = {"pk": str(role.pk)}
        request = types.SimpleNamespace(data={"name": "新角色名"})
        snapshot = build_target_snapshot(view, request)
        assert snapshot["pk"] == str(role.pk)
        changes = {item["field"]: item for item in snapshot["changes"]}
        assert changes["name"]["old"] == "原角色名"
        assert changes["name"]["new"] == "新角色名"
        assert changes["name"]["label"]  # label 可读（序列化器 label 或字段名兜底）

    def test_snapshot_missing_for_unreachable_object(self):
        from approval.utils.approval.snapshot import build_target_snapshot
        from system.views.admin.role import RoleViewSet

        view = RoleViewSet()
        view.action = "update"
        view.kwargs = {"pk": "00000000-0000-0000-0000-000000000000"}
        assert build_target_snapshot(view, types.SimpleNamespace(data={})) == {}


class TestRelatedObject:
    @staticmethod
    def _flow():
        return ApprovalFlow.objects.create(name="U1 流程", code="u1_flow")

    def test_leave_related_object(self, superuser):
        from approval.models.leave import Leave

        flow = self._flow()
        leave = Leave.objects.create(
            leave_type="annual",
            start_date="2031-05-01",
            end_date="2031-05-02",
            days=2,
            reason="U1 关联卡片",
            creator=superuser,
        )
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="请假申请", creator=superuser, biz_type="leave", biz_id=str(leave.pk)
        )
        from approval.utils.approval_flow.biz import biz_summary

        summary = biz_summary(instance)
        assert summary["type"] == "leave"
        assert summary["missing"] is False
        # CI 无 .mo 编译产物时显示英文（首字母大写），断言需大小写不敏感
        title = summary["title"].lower()
        assert "年假" in title or "annual" in title

    def test_unknown_biz_type_degrades(self, superuser):
        from approval.utils.approval_flow.biz import biz_summary

        flow = self._flow()
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="x", creator=superuser, biz_type="no_such_biz", biz_id="1"
        )
        summary = biz_summary(instance)
        assert summary == {
            "type": "no_such_biz",
            "label": "no_such_biz",
            "title": "",
            "status": "",
            "fields": [],
            "missing": True,
        }

    def test_missing_business_row_degrades(self, superuser):
        from approval.utils.approval_flow.biz import biz_summary

        flow = self._flow()
        instance = ApprovalInstance.objects.create(
            flow=flow,
            flow_name=flow.name,
            title="x",
            creator=superuser,
            biz_type="leave",
            biz_id="00000000-0000-0000-0000-000000000001",
        )
        assert biz_summary(instance)["missing"] is True

    def test_instance_detail_api_includes_related_object(self, superuser, auth_client):
        from approval.models.leave import Leave

        flow = self._flow()
        leave = Leave.objects.create(
            leave_type="sick",
            start_date="2031-06-01",
            end_date="2031-06-01",
            days=1,
            reason="感冒",
            creator=superuser,
        )
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="病假", creator=superuser, biz_type="leave", biz_id=str(leave.pk)
        )
        body = auth_client.get(f"/api/system/approval-instances/{instance.pk}").json()["data"]
        assert body["biz_type"] == "leave"
        assert body["related_object"]["type"] == "leave"


class TestAssigneeSnapshot:
    def test_node_task_and_actor_display(self, superuser, approver):
        from approval.utils.approval_flow.engine import _enter_node, approve_task

        flow = ApprovalFlow.objects.create(name="快照流程", code="u1_snapshot_flow")
        node = ApprovalFlowNode.objects.create(
            flow=flow, name="审批节点", order=1, approve_type="OR", assignee_type="user", assignee_value="u1_approver"
        )
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="快照实例", creator=superuser, current_node=node
        )
        _enter_node(instance, node)
        task = ApprovalNodeTask.objects.get(instance=instance)
        assert task.assignee_display == "审批人甲"  # 昵称优先

        ok, detail = approve_task(task.pk, approver)
        assert ok is True, detail
        task.refresh_from_db()
        assert task.actor_display == "审批人甲"
        assert task.status == ApprovalNodeTask.Status.APPROVED

    def test_flat_request_approver_display(self, auth_client, approver):
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"^/api/system/role/"])
        role = UserRole.objects.create(name="快照角色", code="u1_role4")
        auth_client.delete(f"/api/system/role/{role.pk}")
        approval = ApprovalRequest.objects.get(status=ApprovalRequest.Status.PENDING)
        from approval.utils.approval import approve_request

        ok, detail = approve_request(approval, approver)
        assert ok is True, detail
        approval.refresh_from_db()
        assert approval.approver_display == "审批人甲"
