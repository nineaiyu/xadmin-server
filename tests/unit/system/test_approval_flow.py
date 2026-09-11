# -*- coding: utf-8 -*-
"""全量审批流引擎一期（ADR-012）：引擎推进 + API + 页签取值域 + 定时任务。"""

import datetime

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _gettext

from common.core.config import SysConfig
from system.models import DeptInfo, UserInfo, UserRole
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from system.utils.approval_flow import (
    add_sign,
    approve_task,
    cancel_instance,
    clean_finished_instances,
    create_instance,
    eval_condition,
    pending_count_for,
    reject_task,
    remind_pending_tasks,
    resolve_assignees,
    validate_form,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver_role(db):
    return UserRole.objects.create(name="审批人", code="flow_approver")


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="flow_applicant", password="Test@123456", nickname="申请人")


@pytest.fixture
def approver(approver_role):
    user = UserInfo.objects.create_user(username="flow_approver1", password="Test@123456", nickname="审批人一")
    user.roles.add(approver_role)
    return user


@pytest.fixture
def approver2(approver_role):
    user = UserInfo.objects.create_user(username="flow_approver2", password="Test@123456", nickname="审批人二")
    user.roles.add(approver_role)
    return user


def make_flow(code="leave", nodes=None, form_schema=None, is_active=True):
    """快捷构造流程定义；nodes 为 (name, order, 附加字段) 列表。"""
    flow = ApprovalFlow.objects.create(
        name=f"流程-{code}", code=code, form_schema=form_schema or [], is_active=is_active
    )
    for index, node in enumerate(nodes or []):
        params = dict(
            name=node.get("name") or f"节点{index + 1}",
            order=node.get("order") or index + 1,
            approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
            assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
            assignee_value=node.get("assignee_value", "flow_approver"),
            condition=node.get("condition") or {},
            timeout_hours=node.get("timeout_hours") or 0,
        )
        ApprovalFlowNode.objects.create(flow=flow, **params)
    return flow


class TestConditionAndAssignee:
    def test_eval_condition_ops(self):
        data = {"amount": 1000, "reason": "出差", "tags": ["a"]}
        assert eval_condition({}, data) is True
        assert eval_condition(None, data) is True
        assert eval_condition({"field": "amount", "op": "gte", "value": 1000}, data) is True
        assert eval_condition({"field": "amount", "op": "gt", "value": 1000}, data) is False
        assert eval_condition({"field": "amount", "op": "lt", "value": "2000"}, data) is True
        assert eval_condition({"field": "reason", "op": "contains", "value": "出"}, data) is True
        assert eval_condition({"field": "reason", "op": "ne", "value": "出差"}, data) is False
        assert eval_condition({"field": "tags", "op": "in", "value": ["a", "b"]}, data) is True
        assert eval_condition({"field": "missing", "op": "is_empty", "value": None}, data) is True
        assert eval_condition({"field": "missing", "op": "not_empty", "value": None}, data) is False
        # 非法比较（字符串与数字）返回 False，不抛异常
        assert eval_condition({"field": "reason", "op": "gte", "value": 10}, data) is False
        # 未知运算符：跳过该节点
        assert eval_condition({"field": "amount", "op": "unknown", "value": 1}, data) is False

    def test_resolve_assignees_by_type(self, applicant, approver, approver2):
        flow = make_flow()
        role_node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="角色",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.ROLE,
            assignee_value="flow_approver",
        )
        assert {u.pk for u in resolve_assignees(role_node, applicant, {})} == {approver.pk, approver2.pk}

        user_node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="指定用户",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="flow_approver1, flow_approver2",
        )
        assert len(resolve_assignees(user_node, applicant, {})) == 2

        field_node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="表单字段",
            order=3,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="approvers",
        )
        assert len(resolve_assignees(field_node, applicant, {"approvers": ["flow_approver1"]})) == 1
        # 字段缺失 → 无候选（发起会被拒绝）
        assert resolve_assignees(field_node, applicant, {}) == []

    def test_resolve_assignees_leader_and_self_exclusion(self, applicant, approver):
        dept = DeptInfo.objects.create(name="研发部", code="dev_flow", leader=approver)
        applicant.dept = dept
        applicant.save(update_fields=["dept"])
        flow = make_flow()
        node = ApprovalFlowNode.objects.create(
            flow=flow, name="上级", order=1, assignee_type=ApprovalFlowNode.AssigneeType.LEADER
        )
        assert [u.pk for u in resolve_assignees(node, applicant, {})] == [approver.pk]
        # 审批人 == 申请人本人时剔除，避免自审
        dept.leader = applicant
        dept.save(update_fields=["leader"])
        assert resolve_assignees(node, applicant, {}) == []
        # 无部门 → 无候选
        applicant.dept = None
        applicant.save(update_fields=["dept"])
        assert resolve_assignees(node, applicant, {}) == []

    def test_validate_form_required(self):
        flow = ApprovalFlow.objects.create(
            name="报销",
            code="expense",
            form_schema=[{"key": "amount", "label": "金额", "type": "number", "required": True}],
        )
        assert validate_form(flow, {"amount": 100}) is None
        assert "金额" in validate_form(flow, {})


class TestEngineFlow:
    def test_or_sign_advances_and_cancels_others(self, applicant, approver, approver2):
        flow = make_flow(
            nodes=[
                {"name": "初审"},
                {
                    "name": "终审",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow_approver2",
                },
            ]
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="请假申请", form_data={"days": 2})
        assert error is None
        tasks = list(instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING))
        assert len(tasks) == 2  # 初审为角色节点：两名候选

        ok, detail = approve_task(tasks[0].pk, approver, "同意")
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 2
        # 或签其余候选作废
        assert instance.tasks.filter(node_order=1, status=ApprovalNodeTask.Status.CANCELLED).count() == 1
        # 终审由指定用户处理 → 实例通过
        final_task = instance.tasks.get(node_order=2, status=ApprovalNodeTask.Status.PENDING)
        ok, detail = approve_task(final_task.pk, approver2, "")
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        assert instance.finished_at is not None
        assert instance.current_node is None

    def test_and_sign_requires_all(self, applicant, approver, approver2):
        flow = make_flow(nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="会签申请", form_data={})
        assert error is None
        first = instance.tasks.get(assignee=approver, status=ApprovalNodeTask.Status.PENDING)
        ok, _detail = approve_task(first.pk, approver)
        assert ok
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 1  # 会签未全部通过：停留原节点
        second = instance.tasks.get(assignee=approver2, status=ApprovalNodeTask.Status.PENDING)
        ok, _detail = approve_task(second.pk, approver2)
        assert ok
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_reject_terminates_instance(self, applicant, approver):
        flow = make_flow(nodes=[{"name": "初审"}, {"name": "终审"}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="驳回申请", form_data={})
        assert error is None
        task = instance.tasks.filter(node_order=1).first()
        ok, detail = reject_task(task.pk, approver, "材料不全")
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.REJECTED
        assert instance.reason == "材料不全"
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 0
        # 驳回原因必填
        flow2 = make_flow(code="leave2", nodes=[{"name": "初审"}])
        instance2, _ = create_instance(flow=flow2, applicant=applicant, title="x", form_data={})
        task2 = instance2.tasks.first()
        ok, detail = reject_task(task2.pk, approver, "   ")
        assert not ok and detail

    def test_cancel_only_applicant_and_pending(self, applicant, approver):
        flow = make_flow(nodes=[{"name": "初审"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="撤回申请", form_data={})
        ok, _detail = cancel_instance(instance, approver)
        assert not ok  # 非申请人不能撤回
        ok, _detail = cancel_instance(instance, applicant)
        assert ok
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.CANCELLED
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 0

    def test_condition_skips_node(self, applicant, approver, approver2):
        flow = make_flow(
            nodes=[
                {
                    "name": "小额直审",
                    "condition": {"field": "amount", "op": "lte", "value": 100},
                    "assignee_value": "flow_approver1",
                },
                {
                    "name": "大额终审",
                    "condition": {"field": "amount", "op": "gt", "value": 100},
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow_approver2",
                },
            ]
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="条件申请", form_data={"amount": 500})
        assert error is None
        # 首节点的条件不命中 → 直接进入第二节点
        assert instance.current_node.order == 2
        assert instance.tasks.filter(node_order=1).count() == 0

    def test_guard_rails(self, applicant, approver, superuser):
        flow = make_flow(nodes=[{"name": "初审"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="越权申请", form_data={})
        task = instance.tasks.first()
        # 非指派用户不能处理
        ok, detail = approve_task(task.pk, superuser)
        assert not ok
        # 文案断言用 gettext 同源取值：本机有 .mo 时是中文、CI 无 .mo 时是英文，
        # 写死任一种语言都会造成跨环境假红（历史教训）
        assert detail == str(_gettext("This task is not assigned to you"))
        # 申请人不能处理自己的申请（指派经过剔除，这里用直接调用兜底校验）
        ok, detail = approve_task(task.pk, applicant)
        assert not ok
        # 重复处理
        ok, _detail = approve_task(task.pk, approver)
        assert ok
        ok, detail = approve_task(task.pk, approver)
        assert not ok

    def test_create_instance_fail_closed(self, applicant, approver):
        # 无可达节点
        flow = ApprovalFlow.objects.create(name="空流程", code="empty_flow")
        instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert instance is None and error
        # 节点无候选（角色无成员）
        flow2 = make_flow(code="no_candidate", nodes=[{"name": "无人", "assignee_value": "not_exists_role"}])
        instance, error = create_instance(flow=flow2, applicant=applicant, title="x", form_data={})
        assert instance is None
        assert "无人" in error
        # 停用的流程不可发起
        flow3 = make_flow(code="disabled", nodes=[{"name": "节点"}], is_active=False)
        ok_instance, _error = create_instance(flow=flow3, applicant=applicant, title="x", form_data={})
        assert ok_instance is None

    def test_add_sign_joins_current_node(self, applicant, approver, approver2):
        flow = make_flow(
            nodes=[
                {
                    "name": "会签",
                    "approve_type": ApprovalFlowNode.ApproveType.AND,
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow_approver1",
                }
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="加签申请", form_data={})
        # 非节点参与人不能加签
        outsider = UserInfo.objects.create_user(username="outsider", password="Test@123456")
        ok, _detail = add_sign(instance, outsider, "flow_approver2")
        assert not ok
        ok, detail = add_sign(instance, approver, "flow_approver2", "请协助")
        assert ok, detail
        added = instance.tasks.filter(node=instance.current_node, assignee=approver2, is_added=True)
        assert added.exists()
        # 会签语义：新增审批人必须通过
        for task in instance.tasks.filter(node=instance.current_node, status=ApprovalNodeTask.Status.PENDING):
            approve_task(task.pk, task.assignee)
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        # 重复加签被拒
        ok, _detail = add_sign(instance, approver, "flow_approver2")
        assert not ok

    def test_pending_count_and_remind_and_clean(self, applicant, approver, monkeypatch):
        flow = make_flow(nodes=[{"name": "超时节点", "timeout_hours": 1}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="提醒申请", form_data={})
        assert pending_count_for(approver) == 1
        assert pending_count_for(applicant) == 0  # 申请人自己的申请不计入待办

        # 未超时：不提醒
        assert remind_pending_tasks() == 0
        # 手工把任务创建时间前移 2 小时 → 命中超时提醒；同任务第二次调用被缓存占位去重
        old = timezone.now() - datetime.timedelta(hours=2)
        ApprovalNodeTask.objects.filter(instance=instance).update(created_time=old)
        assert remind_pending_tasks() == 1
        assert remind_pending_tasks() == 0

        # 保留期清理：实例超过保留期即删除（级联任务）
        monkeypatch.setattr(type(SysConfig), "APPROVAL_FLOW_KEEP_DAYS", property(lambda self: 1), raising=False)
        ApprovalInstance.objects.filter(pk=instance.pk).update(created_time=old - datetime.timedelta(days=3))
        removed = clean_finished_instances()
        assert removed >= 1
        assert not ApprovalInstance.objects.filter(pk=instance.pk).exists()
        assert not ApprovalNodeTask.objects.filter(instance_id=instance.pk).exists()
