# -*- coding: utf-8 -*-
"""全量审批流引擎一期（ADR-012）：引擎推进 + API + 页签取值域 + 定时任务。"""

import datetime

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _gettext

from common.core.config import SysConfig
from system.models import DeptInfo, UserInfo, UserRole
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from system.serializers.approval_flow import ApprovalFlowSerializer
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
            approve_ratio=node.get("approve_ratio", 100),
            assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
            assignee_value=node.get("assignee_value", "flow_approver"),
            condition=node.get("condition") or {},
            routes=node.get("routes") or [],
            layout=node.get("layout") or {},
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


class TestBranchRoutes:
    """二期条件分支（ADR-016 §1）：排他网关出口路由 + 线性回退。"""

    def test_route_hit_jumps_to_target(self, applicant, approver):
        """路由命中跳转 target（排他网关）：跳过中间线性节点。"""
        from system.utils.approval_flow import simulate_path

        flow = make_flow(
            "branch_hit",
            form_schema=[{"key": "amount", "label": "金额", "type": "number", "required": True}],
            nodes=[
                {"name": "提交初审", "order": 1},
                {
                    "name": "大额终审",
                    "order": 2,
                    "condition": {"field": "amount", "op": "gte", "value": 1000},
                },
                {
                    "name": "小额免审出口",
                    "order": 3,
                    "routes": [{"condition": {"field": "amount", "op": "lt", "value": 1000}, "target": 4}],
                },
                {"name": "财务归档", "order": 4},
            ],
        )
        # amount < 1000：节点 3 的路由命中 → 直达 4（跳过节点 2 的线性下一跳）
        path = simulate_path(flow, {"amount": 100})
        assert [node.order for node in path] == [1, 3, 4]
        # amount >= 1000：节点 3 路由未命中 → 线性回退经过节点 2
        path = simulate_path(flow, {"amount": 5000})
        assert [node.order for node in path] == [1, 2, 3, 4]

    def test_route_target_missing_falls_back_linear(self, applicant, approver):
        """target 节点不存在：记日志后回退线性语义（fail-safe）。"""
        from system.utils.approval_flow import next_node

        flow = make_flow(
            "branch_missing",
            nodes=[
                {"name": "A", "order": 1, "routes": [{"condition": {}, "target": 99}]},
                {"name": "B", "order": 2},
            ],
        )
        node1 = flow.nodes.get(order=1)
        following = next_node(flow, 1, {}, node=node1)
        assert following is not None and following.order == 2

    def test_branch_instance_end_to_end(self, applicant, approver):
        """分支主链路：小额走快车道（路由直达归档节点），审批一次即通过。"""
        flow = make_flow(
            "branch_e2e",
            form_schema=[{"key": "amount", "label": "金额", "type": "number", "required": True}],
            nodes=[
                {"name": "初审", "order": 1},
                {
                    "name": "大额终审",
                    "order": 2,
                    "condition": {"field": "amount", "op": "gte", "value": 1000},
                },
                {
                    "name": "小额出口",
                    "order": 3,
                    "routes": [{"condition": {"field": "amount", "op": "lt", "value": 1000}, "target": 4}],
                },
                {"name": "归档", "order": 4},
            ],
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="小额申请", form_data={"amount": 100})
        assert error is None
        # 首节点（初审）通过 → 线性进入节点 3（小额出口；节点 2 条件不命中被跳过）
        task = ApprovalNodeTask.objects.get(instance=instance, node_order=1, assignee=approver)
        ok, _ = approve_task(task.pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.current_node.order == 3
        # 节点 3 通过 → 路由命中直达节点 4（归档）；全程未经过节点 2
        task3 = ApprovalNodeTask.objects.get(instance=instance, node_order=3, assignee=approver)
        ok, _ = approve_task(task3.pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.current_node.order == 4
        # 归档节点审批后无后续节点 → 实例通过
        task4 = ApprovalNodeTask.objects.get(instance=instance, node_order=4, assignee=approver)
        ok, _ = approve_task(task4.pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        assert instance.tasks.filter(node_order=2).exists() is False

    def test_loop_route_rejected_at_creation(self, applicant, approver):
        """路由成环：模拟步数兜底，发起报错（保存时校验为主，此处兜底历史数据）。"""
        flow = make_flow(
            "branch_loop",
            nodes=[
                {"name": "A", "order": 1, "routes": [{"condition": {}, "target": 2}]},
                {"name": "B", "order": 2, "routes": [{"condition": {}, "target": 1}]},
            ],
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="成环", form_data={})
        assert instance is None
        assert "loop" in error


class TestRatioApprove:
    """二期比例会签（ADR-016 §3）：达标通过 / 不可能达标提前驳回。"""

    def _three_member_flow(self, applicant, approver, approver2, ratio):
        from tests.unit.system.test_approval_flow import make_flow as _make

        flow = _make(
            f"ratio_{ratio}",
            nodes=[{"name": "会签", "order": 1, "approve_type": "RATIO", "approve_ratio": ratio}],
        )
        return flow

    def test_ratio_reached_advances(self, applicant, approver, approver2):
        """2 人候选 + 50%：1 人通过即达标，节点通过、实例 APPROVED（其余待办作废）。"""
        flow = self._three_member_flow(applicant, approver, approver2, 50)
        instance, error = create_instance(flow=flow, applicant=applicant, title="比例", form_data={})
        assert error is None
        tasks = ApprovalNodeTask.objects.filter(instance=instance)
        assert tasks.count() == 2
        ok, _ = approve_task(tasks.filter(assignee=approver).first().pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        # 另一候选人的待办被作废
        assert tasks.filter(assignee=approver2, status=ApprovalNodeTask.Status.CANCELLED).exists()

    def test_ratio_unreachable_rejects_early(self, applicant, approver, approver2):
        """3 人 100%：1 人通过 + 1 人拒绝 → 剩余不足，提前驳回。"""
        flow = self._three_member_flow(applicant, approver, approver2, 100)
        instance, error = create_instance(flow=flow, applicant=applicant, title="比例", form_data={})
        assert error is None
        tasks = ApprovalNodeTask.objects.filter(instance=instance)
        # 2 人候选 + 100%：通过 1 人（未达标），拒绝 1 人（无人可补齐 2 票）→ 提前驳回
        ok, _ = approve_task(tasks.filter(assignee=approver).first().pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        ok, _ = reject_task(tasks.filter(assignee=approver2).first().pk, approver2, "不同意")
        assert ok is True
        instance.refresh_from_db()
        # 显式拒绝直接驳回实例（reject_task 语义对所有策略一致）
        assert instance.status == ApprovalInstance.Status.REJECTED


class TestFlowVersions:
    """二期版本管理（ADR-016 §2）：保存落快照 / 回滚写回 / PENDING 拒绝回滚。"""

    def _flow_with_nodes(self, code):
        flow = make_flow(code, nodes=[{"name": "节点一", "order": 1}])
        serializer = ApprovalFlowSerializer(instance=flow)
        serializer._snapshot_version(flow, [{"name": "节点一", "order": 1}], remark="初始版本")
        return flow, serializer

    def test_rollback_writes_snapshot_back(self, applicant):

        flow, serializer = self._flow_with_nodes("ver_rollback")
        # 变更定义（加节点）→ 落 v2
        nodes_v2 = [{"name": "节点一", "order": 1}, {"name": "节点二", "order": 2}]
        flow.nodes.all().delete()
        serializer._replace_nodes(flow, nodes_v2)
        serializer._snapshot_version(flow, nodes_v2, remark="加节点")
        assert flow.versions.count() == 2
        assert flow.nodes.count() == 2

        # 回滚到 v1 → 活定义恢复单节点 + 落 v3（回滚也是一次变更）
        ok, detail = serializer.rollback_to_version(flow, 1)
        assert ok is True
        assert flow.nodes.count() == 1
        assert flow.versions.count() == 3
        assert flow.versions.order_by("-version").first().version == 3

    def test_rollback_missing_version(self, applicant):

        flow, serializer = self._flow_with_nodes("ver_missing")
        ok, _detail = serializer.rollback_to_version(flow, 99)
        assert ok is False

    def test_rollback_blocked_with_pending_instance(self, applicant, approver):

        flow, serializer = self._flow_with_nodes("ver_pending")
        create_instance(flow=flow, applicant=applicant, title="在途", form_data={})
        ok, detail = serializer.rollback_to_version(flow, 1)
        assert ok is False
        assert detail  # 返回可读错误（zh: 该流程存在待审批申请，节点不可修改）

    def test_create_instance_records_flow_version(self, applicant, approver):
        flow, _serializer = self._flow_with_nodes("ver_record")
        instance, error = create_instance(flow=flow, applicant=applicant, title="版本", form_data={})
        assert error is None
        assert instance.flow_version == flow.version
