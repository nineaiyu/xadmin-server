# -*- coding: utf-8 -*-
"""请假业务 API 集成（ADR-032）：权限 + 新增即提交 + 取值域 + 撤回 + 与流程引擎联动。

覆盖「业务单 ↔ 审批实例」的完整闭环：请假接口发起 → 流程审批中心通过 → 请假单状态回写。
"""

import datetime

import pytest
from rest_framework.test import APIClient
from django.utils import timezone

from system.models import Menu, UserInfo
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from system.models.leave import Leave

pytestmark = pytest.mark.django_db

LEAVES_URL = "/api/system/leaves"
INSTANCES_URL = "/api/system/approval-instances"


def make_leave_flow(code="leave", assignee_value="leave_approver"):
    flow = ApprovalFlow.objects.create(
        name="请假审批",
        code=code,
        form_schema=[
            {"key": "days", "label": "请假天数", "type": "number", "required": True},
            {"key": "reason", "label": "请假事由", "type": "textarea", "required": True},
        ],
    )
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="直属主管审批",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=assignee_value,
        timeout_hours=24,
    )
    return flow


def grant(role, menu_factory, name, path, method):
    perm = Menu.objects.filter(name=name).first() or menu_factory(name, path=path, method=method)
    role.menu.add(perm)
    return perm


APPROVER_PERMS = (
    ("list:SystemApprovalInstance", "api/system/approval-instances$", "GET"),
    ("approve:SystemApprovalInstance", "api/system/approval-instances/(?P<pk>[^/.]+)/approve$", "POST"),
    ("reject:SystemApprovalInstance", "api/system/approval-instances/(?P<pk>[^/.]+)/reject$", "POST"),
)


@pytest.fixture
def approver(db, role, menu_factory):
    """审批人：具备流程审批中心（待办列表 + 通过/驳回）权限。"""
    user = UserInfo.objects.create_user(username="leave_approver", password="Test@123456", nickname="主管")
    user.roles.add(role)
    for name, path, method in APPROVER_PERMS:
        grant(role, menu_factory, name, path, method)
    return user


@pytest.fixture
def approver_client(approver):
    """独立客户端：force_authenticate 会互相覆盖，不能与申请人共用同一 APIClient。"""
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=approver)
    return client


LEAVE_PERMS = (
    ("list:SystemLeave", "api/system/leaves$", "GET"),
    ("create:SystemLeave", "api/system/leaves$", "POST"),
    ("retrieve:SystemLeave", "api/system/leaves/(?P<pk>[^/.]+)$", "GET"),
    ("destroy:SystemLeave", "api/system/leaves/(?P<pk>[^/.]+)$", "DELETE"),
    ("submit:SystemLeave", "api/system/leaves/(?P<pk>[^/.]+)/submit$", "POST"),
    ("cancel:SystemLeave", "api/system/leaves/(?P<pk>[^/.]+)/cancel$", "POST"),
)


@pytest.fixture
def applicant_client(db, role, menu_factory):
    """普通申请人：按 seed 的权限码授权（非超管必须命中菜单权限，否则 403）。"""
    user = UserInfo.objects.create_user(username="leave_applicant", password="Test@123456", nickname="申请人")
    user.roles.add(role)
    for name, path, method in LEAVE_PERMS:
        grant(role, menu_factory, name, path, method)
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


def payload(**kwargs):
    today = timezone.localdate()
    data = {
        "leave_type": "annual",
        "start_date": today.isoformat(),
        "end_date": (today + datetime.timedelta(days=2)).isoformat(),
        "days": "3.0",
        "reason": "年假出行",
    }
    data.update(kwargs)
    return data


class TestLeaveApiFlow:
    def test_create_auto_submits_and_binds_instance(self, auth_client, approver):
        make_leave_flow()
        response = auth_client.post(LEAVES_URL, payload(), format="json")
        assert response.data["code"] == 1000
        data = response.data["data"]
        assert data["status"]["value"] == "PENDING"
        assert data["instance_pk"]
        assert data["current_node_name"] == "直属主管审批"

        leave = Leave.objects.get(pk=data["pk"])
        assert leave.instance.biz_type == "leave"
        assert leave.instance.biz_id == str(leave.pk)
        assert ApprovalNodeTask.objects.filter(instance=leave.instance, assignee=approver).exists()

    def test_create_without_flow_keeps_draft(self, auth_client):
        response = auth_client.post(LEAVES_URL, payload(), format="json")
        assert response.data["code"] == 1000
        assert response.data["data"]["status"]["value"] == "DRAFT"
        assert "草稿" in str(response.data["detail"]) or "draft" in str(response.data["detail"]).lower()

    def test_approve_in_center_syncs_leave_status(self, auth_client, approver_client, applicant_client, approver):
        make_leave_flow()
        created = applicant_client.post(LEAVES_URL, payload(), format="json")
        assert created.data["code"] == 1000
        leave_pk = created.data["data"]["pk"]
        instance_pk = created.data["data"]["instance_pk"]

        # 审批人有待办
        pending = approver_client.get(INSTANCES_URL, {"scope": "pending"})
        assert pending.data["data"]["total"] == 1
        approved = approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"comment": "同意"}, format="json")
        assert approved.data["code"] == 1000

        leave = Leave.objects.get(pk=leave_pk)
        assert leave.status == Leave.Status.APPROVED
        assert leave.instance.status == ApprovalInstance.Status.APPROVED
        # 业务侧的只读派生字段随实例更新
        detail = applicant_client.get(f"{LEAVES_URL}/{leave_pk}")
        assert detail.data["data"]["status"]["value"] == "APPROVED"
        assert detail.data["data"]["current_node_name"] == ""

    def test_reject_reason_surfaces_on_business_row(self, applicant_client, approver_client, approver):
        make_leave_flow()
        created = applicant_client.post(LEAVES_URL, payload(), format="json")
        instance_pk = created.data["data"]["instance_pk"]
        rejected = approver_client.post(
            f"{INSTANCES_URL}/{instance_pk}/reject", {"reason": "请补充证明材料"}, format="json"
        )
        assert rejected.data["code"] == 1000
        leave = Leave.objects.get(pk=created.data["data"]["pk"])
        assert leave.status == Leave.Status.REJECTED
        detail = applicant_client.get(f"{LEAVES_URL}/{leave.pk}")
        assert detail.data["data"]["reject_reason"] == "请补充证明材料"

    def test_cancel_endpoint(self, applicant_client, approver):
        make_leave_flow()
        created = applicant_client.post(LEAVES_URL, payload(), format="json")
        leave_pk = created.data["data"]["pk"]
        cancelled = applicant_client.post(f"{LEAVES_URL}/{leave_pk}/cancel", {}, format="json")
        assert cancelled.data["code"] == 1000
        assert Leave.objects.get(pk=leave_pk).status == Leave.Status.CANCELLED
        # 撤回后可重新提交（同一业务单，换新实例）
        resubmitted = applicant_client.post(f"{LEAVES_URL}/{leave_pk}/submit", {}, format="json")
        assert resubmitted.data["code"] == 1000
        leave = Leave.objects.get(pk=leave_pk)
        assert leave.status == Leave.Status.PENDING
        assert ApprovalInstance.objects.filter(biz_type="leave", biz_id=str(leave_pk)).count() == 2

    def test_overlapping_period_conflict(self, applicant_client, approver):
        make_leave_flow()
        assert applicant_client.post(LEAVES_URL, payload(), format="json").data["code"] == 1000
        today = timezone.localdate()
        conflict = applicant_client.post(
            LEAVES_URL,
            payload(
                start_date=today.isoformat(), end_date=(today + datetime.timedelta(days=1)).isoformat(), days="2.0"
            ),
            format="json",
        )
        assert conflict.data["code"] != 1000

    def test_destroy_guards(self, applicant_client, auth_client, approver):
        make_leave_flow()
        created = applicant_client.post(LEAVES_URL, payload(), format="json")
        leave_pk = created.data["data"]["pk"]
        denied = applicant_client.delete(f"{LEAVES_URL}/{leave_pk}")
        assert denied.data["code"] != 1000  # 审批中不可删除
        applicant_client.post(f"{LEAVES_URL}/{leave_pk}/cancel", {}, format="json")
        assert auth_client.delete(f"{LEAVES_URL}/{leave_pk}").data["code"] == 1000

    def test_visibility_scope(self, applicant_client, auth_client):
        """普通用户只看到自己相关（本用例：仅本人提交），超管全量。"""
        applicant = UserInfo.objects.get(username="leave_applicant")
        today = timezone.localdate()
        Leave.objects.create(
            creator=applicant,
            modifier=applicant,
            leave_type="annual",
            start_date=today,
            end_date=today,
            days="1.0",
            reason="自己的单",
            status=Leave.Status.DRAFT,
        )
        outsider = UserInfo.objects.create_user(username="leave_outsider", password="Test@123456")
        Leave.objects.create(
            creator=outsider,
            modifier=outsider,
            leave_type="sick",
            start_date=today,
            end_date=today,
            days="1.0",
            reason="别人的单",
            status=Leave.Status.DRAFT,
        )
        mine = applicant_client.get(LEAVES_URL)
        assert mine.data["data"]["total"] == 1
        assert mine.data["data"]["results"][0]["reason"] == "自己的单"
        assert auth_client.get(LEAVES_URL).data["data"]["total"] == 2


class TestLeavePermission:
    def test_list_requires_menu_permission(self, api_client, normal_user, role, menu_factory):
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(LEAVES_URL).status_code == 403
        grant(role, menu_factory, "list:SystemLeave", "api/system/leaves$", "GET")
        assert api_client.get(LEAVES_URL).data["code"] == 1000

    def test_create_requires_menu_permission(self, api_client, normal_user, role, menu_factory):
        api_client.force_authenticate(user=normal_user)
        grant(role, menu_factory, "list:SystemLeave", "api/system/leaves$", "GET")
        assert api_client.post(LEAVES_URL, payload(), format="json").status_code == 403
        grant(role, menu_factory, "create:SystemLeave", "api/system/leaves$", "POST")
        assert api_client.post(LEAVES_URL, payload(), format="json").data["code"] == 1000
