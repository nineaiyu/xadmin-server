# -*- coding: utf-8 -*-
"""请假业务挂接审批流引擎（ADR-032）：业务校验 / 提交绑定 / 终态回写 / 撤回。

守护三件事：
1. 业务校验（日期倒挂、天数超跨度、区间冲突）在接口层之前就拦住脏数据；
2. 提交时创建带 biz_type/biz_id 的实例，审批终态经信号回写业务单状态；
3. 流程缺失 / 节点无候选时 fail-closed 拒绝提交（保留草稿），绝不静默直通。
"""

import datetime

import pytest
from django.utils import timezone

from system.models import UserInfo, UserRole
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from system.models.leave import Leave
from system.utils.leave import resolve_leave_flow, submit_leave, validate_leave_payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver_role(db):
    return UserRole.objects.create(name="请假审批人", code="leave_approver")


@pytest.fixture
def approver(approver_role):
    user = UserInfo.objects.create_user(username="leave_approver", password="Test@123456", nickname="主管")
    user.roles.add(approver_role)
    return user


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="leave_applicant", password="Test@123456", nickname="申请人")


def make_leave_flow(code="leave", assignee_type=ApprovalFlowNode.AssigneeType.USER, assignee_value="leave_approver"):
    """构造单节点请假流程（默认为指定用户，便于断言）。"""
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
        assignee_type=assignee_type,
        assignee_value=assignee_value,
        timeout_hours=24,
    )
    return flow


def make_leave(applicant, **kwargs):
    today = timezone.localdate()
    params = {
        "leave_type": Leave.LeaveType.ANNUAL,
        "start_date": today,
        "end_date": today + datetime.timedelta(days=2),
        "days": "3.0",
        "reason": "年假出行",
        "status": Leave.Status.DRAFT,
        "creator": applicant,
        "modifier": applicant,
    }
    params.update(kwargs)
    return Leave.objects.create(**params)


class TestLeaveValidation:
    def test_date_and_days_rules(self, applicant):
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)
        assert validate_leave_payload(start_date=tomorrow, end_date=today)  # 结束早于开始
        assert validate_leave_payload(start_date=today, end_date=tomorrow, days="9")  # 超过跨度（2 天）
        assert validate_leave_payload(start_date=today, end_date=tomorrow, days="0")  # 天数必须 > 0
        assert validate_leave_payload(start_date=today, end_date=tomorrow, days="1.5") is None  # 允许半天
        # 缺省天数 = 起止跨度（含首尾）
        assert validate_leave_payload(start_date=today, end_date=tomorrow, days=None) is None

    def test_overlapping_period_rejected(self, applicant):
        today = timezone.localdate()
        make_leave(applicant, start_date=today, end_date=today + datetime.timedelta(days=2), days="3.0")
        error = validate_leave_payload(
            start_date=today + datetime.timedelta(days=2),
            end_date=today + datetime.timedelta(days=4),
            days="3.0",
            creator=applicant,
        )
        assert error and "重叠" in error or "overlaps" in error
        # 撤销后的历史单不再占用区间
        Leave.objects.filter(creator=applicant).update(status=Leave.Status.CANCELLED)
        assert (
            validate_leave_payload(
                start_date=today + datetime.timedelta(days=2),
                end_date=today + datetime.timedelta(days=4),
                days="3.0",
                creator=applicant,
            )
            is None
        )


class TestLeaveSubmitAndSync:
    def test_submit_binds_instance_and_syncs_on_approve(self, applicant, approver):
        make_leave_flow()
        leave = make_leave(applicant)
        ok, detail = submit_leave(leave, applicant)
        assert ok, detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.PENDING
        assert leave.instance_id is not None

        instance = leave.instance
        assert instance.biz_type == "leave"
        assert instance.biz_id == str(leave.pk)
        # form_data 随实例走，条件节点/字段审批人按这些 key 取值
        assert instance.form_data["days"] == 3.0
        assert instance.form_data["reason"] == "年假出行"

        task = ApprovalNodeTask.objects.get(instance=instance, assignee=approver)
        from system.utils.approval_flow import approve_task

        ok, detail = approve_task(task.pk, approver, "同意")
        assert ok, detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.APPROVED

    def test_reject_syncs_business_status(self, applicant, approver):
        make_leave_flow()
        leave = make_leave(applicant)
        assert submit_leave(leave, applicant)[0] is True
        task = ApprovalNodeTask.objects.get(instance=leave.instance, assignee=approver)
        from system.utils.approval_flow import reject_task

        ok, detail = reject_task(task.pk, approver, "材料不全")
        assert ok, detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.REJECTED
        assert leave.instance.reason == "材料不全"
        # 驳回后可重新提交（换新实例）
        old_instance_pk = leave.instance_id
        assert submit_leave(leave, applicant)[0] is True
        leave.refresh_from_db()
        assert leave.status == Leave.Status.PENDING
        assert leave.instance_id != old_instance_pk

    def test_cancel_syncs_business_status(self, applicant, approver):
        from system.utils.leave import cancel_leave

        make_leave_flow()
        leave = make_leave(applicant)
        assert submit_leave(leave, applicant)[0] is True
        ok, detail = cancel_leave(leave, approver)
        assert not ok  # 仅申请人可撤回
        ok, detail = cancel_leave(leave, applicant)
        assert ok, detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.CANCELLED
        assert leave.instance.status == ApprovalInstance.Status.CANCELLED

    def test_submit_fail_closed_without_flow_or_candidate(self, applicant):
        # 无任何流程：拒绝提交且保留草稿，不静默直通
        leave = make_leave(applicant)
        ok, detail = submit_leave(leave, applicant)
        assert not ok and detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.DRAFT and leave.instance_id is None

        # 流程存在但节点无候选（角色无成员）：同样拒绝，且不产生实例
        make_leave_flow(code="leave", assignee_type=ApprovalFlowNode.AssigneeType.ROLE, assignee_value="nobody_role")
        ok, detail = submit_leave(leave, applicant)
        assert not ok and detail
        leave.refresh_from_db()
        assert leave.status == Leave.Status.DRAFT
        assert ApprovalInstance.objects.filter(biz_type="leave").count() == 0

    def test_resolve_flow_prefers_config_then_type(self, applicant):
        make_leave_flow(code="leave_personal")
        flow = resolve_leave_flow(Leave.LeaveType.PERSONAL)
        assert flow.code == "leave_personal"
        # 类型专用流程不存在时回退 leave 前缀流程
        flow = resolve_leave_flow(Leave.LeaveType.SICK)
        assert flow.code == "leave_personal"
        # 停用后不再被解析
        ApprovalFlow.objects.filter(code="leave_personal").update(is_active=False)
        assert resolve_leave_flow(Leave.LeaveType.SICK) is None

    def test_unbound_instance_skips_business_callback(self, applicant, approver):
        """未绑业务的实例（历史用法）不触发业务回写，引擎行为保持不变。"""
        make_leave_flow()
        from system.utils.approval_flow import approve_task, create_instance

        instance, error = create_instance(
            flow=ApprovalFlow.objects.get(code="leave"),
            applicant=applicant,
            title="x",
            form_data={"days": 1, "reason": "引擎自带表单"},
        )
        assert error is None
        assert instance.biz_type == ""
        task = ApprovalNodeTask.objects.get(instance=instance, assignee=approver)
        assert approve_task(task.pk, approver)[0] is True
        assert instance.tasks.count() == 1
