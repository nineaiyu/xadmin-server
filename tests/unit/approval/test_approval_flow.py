# -*- coding: utf-8 -*-
"""全量审批流引擎一期：引擎推进 + API + 页签取值域 + 定时任务。"""

import datetime

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _gettext

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from approval.serializers.approval_flow import ApprovalFlowSerializer
from approval.utils.approval_flow import (
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
from common.core.config import SysConfig
from system.models import DeptInfo, Post, UserInfo, UserRole

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

    def test_resolve_assignees_by_post(self, applicant, approver, approver2):
        """post 节点：按岗位 code 解析在岗用户；停用/软删岗位剔除、兼岗去重、申请人剔除。"""
        backend_post = Post.objects.create(name="后端工程师", code="flow_post_backend")
        security_post = Post.objects.create(name="安全员", code="flow_post_security")
        # 兼岗：approver 两岗均命中，去重后只出现一次
        approver.posts.add(backend_post, security_post)
        approver2.posts.add(security_post)
        flow = make_flow()
        node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="岗位",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.POST,
            assignee_value="flow_post_backend, flow_post_security",
        )
        assert {u.pk for u in resolve_assignees(node, applicant, {})} == {approver.pk, approver2.pk}

        # 停用岗位不再参与解析
        security_post.is_active = False
        security_post.save(update_fields=["is_active"])
        assert {u.pk for u in resolve_assignees(node, applicant, {})} == {approver.pk}

        # 软删除（回收站）岗位不参与解析
        security_post.deleted_at = timezone.now()
        security_post.save(update_fields=["deleted_at"])
        assert {u.pk for u in resolve_assignees(node, applicant, {})} == {approver.pk}

        # 申请人持岗 → 剔除本人，避免自审
        security_post.deleted_at = None
        security_post.save(update_fields=["deleted_at"])
        applicant.posts.add(security_post)
        assert {u.pk for u in resolve_assignees(node, applicant, {})} == {approver.pk}

        # 未知岗位 code → 无候选（发起会被拒绝）
        empty_node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="岗位-空",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.POST,
            assignee_value="post_not_exists",
        )
        assert resolve_assignees(empty_node, applicant, {}) == []

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

    def test_cancel_superuser_can_clear_others(self, applicant, approver, superuser):
        """超管可撤回他人 PENDING 申请：运营清障路径。

        演示/离职账号发起的在途单若无人可撤回，会永久阻塞该流程的节点编辑
        （在途实例存在时流程定义不可改动），超管需要能代为收口。
        """
        flow = make_flow(code="leave_superuser_cancel", nodes=[{"name": "初审"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="运营清障", form_data={})
        ok, detail = cancel_instance(instance, superuser)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.CANCELLED

    def test_create_instance_leader_no_approver_detail(self, applicant):
        """leader 节点无候选时给出可操作提示（无部门 / 部门无负责人 / 负责人即申请人）。"""
        node = {"name": "上级审批", "assignee_type": ApprovalFlowNode.AssigneeType.LEADER, "assignee_value": ""}
        flow = make_flow(code="leader_detail", nodes=[node])
        _instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert error == str(
            _gettext(
                "Node {} has no available approver: the applicant has no department yet. "
                "Please assign a department with leader to the applicant first"
            )
        ).format("上级审批")

        dept = DeptInfo.objects.create(name="无主管部门", code="no_leader_dept")
        applicant.dept = dept
        applicant.save(update_fields=["dept"])
        _instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert error == str(
            _gettext(
                "Node {} has no available approver: the applicant's department has no leader. "
                "Please configure a department leader first"
            )
        ).format("上级审批")

        dept.leader = applicant
        dept.save(update_fields=["leader"])
        _instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert error == str(
            _gettext(
                "Node {} has no available approver: the applicant is the department leader. "
                "Please adjust the department leader or the approver of this node"
            )
        ).format("上级审批")

    def test_create_instance_post_no_approver_detail(self, applicant):
        """post 节点无候选时区分「岗位不存在/停用」与「岗位无在岗成员」（可操作提示）。"""
        flow = make_flow(
            code="post_detail",
            nodes=[
                {
                    "name": "岗位审批",
                    "assignee_type": ApprovalFlowNode.AssigneeType.POST,
                    "assignee_value": "post_ghost",
                }
            ],
        )
        _instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert error == str(
            _gettext(
                "Node {} has no available approver: the configured posts do not exist or are disabled. "
                "Please check the post configuration of this node"
            )
        ).format("岗位审批")

        # 岗位存在且启用，但无在岗成员
        post = Post.objects.create(name="安全员", code="post_no_members")
        flow2 = make_flow(
            code="post_detail2",
            nodes=[
                {
                    "name": "岗位审批",
                    "assignee_type": ApprovalFlowNode.AssigneeType.POST,
                    "assignee_value": post.code,
                }
            ],
        )
        _instance, error = create_instance(flow=flow2, applicant=applicant, title="x", form_data={})
        assert error == str(
            _gettext(
                "Node {} has no available approver: no active user holds the configured posts. "
                "Please assign members to the posts first"
            )
        ).format("岗位审批")

        # 补上成员后可正常发起
        holder = UserInfo.objects.create_user(username="post_holder", password="Test@123456")
        holder.posts.add(post)
        instance, error = create_instance(flow=flow2, applicant=applicant, title="x", form_data={})
        assert error is None
        assert instance.tasks.filter(assignee=holder, status=ApprovalNodeTask.Status.PENDING).exists()

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


class TestPendingCountPreciseInvalidation:
    """待办计数缓存精确失效：只清受影响用户，不再全量扫活跃用户清 5000 键。"""

    @staticmethod
    def _record_cleared(monkeypatch):
        from django.core.cache import cache

        cleared = []
        real_delete_many = cache.delete_many

        def recording(keys, *args, **kwargs):
            cleared.extend(keys)
            return real_delete_many(keys, *args, **kwargs)

        monkeypatch.setattr(cache, "delete_many", recording)
        return cleared

    def test_approve_clears_actor_and_cancelled_only(self, applicant, approver, approver2, monkeypatch):
        """或签：一人通过 → 失效集 = 处理人 + 同节点被作废候选；无关用户不被清。"""
        bystander = UserInfo.objects.create_user(username="flow_bystander", password="Test@123456")
        flow = make_flow(nodes=[{"name": "或签", "approve_type": ApprovalFlowNode.ApproveType.OR}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="精确失效", form_data={})
        assert error is None, error
        task = instance.tasks.get(assignee=approver)
        cleared = self._record_cleared(monkeypatch)

        ok, detail = approve_task(task.pk, approver, "同意")
        assert ok, detail
        keys = set(cleared)
        assert f"approval_flow_pending_count_{approver.pk}" in keys  # 处理人
        assert f"approval_flow_pending_count_{approver2.pk}" in keys  # 或签被作废的候选
        assert f"approval_flow_pending_count_{bystander.pk}" not in keys  # 无关用户
        assert f"approval_flow_pending_count_{applicant.pk}" not in keys  # 申请人不在待办口径内
        # 功能回归：受影响者计数即时正确（缓存已精确清）
        assert pending_count_for(approver) == 0

    def test_reject_clears_all_cancelled_assignees_only(self, applicant, approver, approver2, monkeypatch):
        """驳回：失效集 = 处理人 + 整单被作废待办的 assignee。"""
        bystander = UserInfo.objects.create_user(username="flow_bystander2", password="Test@123456")
        flow = make_flow(nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="驳回失效", form_data={})
        assert error is None, error
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 2
        task = instance.tasks.get(assignee=approver)
        cleared = self._record_cleared(monkeypatch)

        ok, detail = reject_task(task.pk, approver, "不符合要求")
        assert ok, detail
        keys = set(cleared)
        assert f"approval_flow_pending_count_{approver.pk}" in keys
        assert f"approval_flow_pending_count_{approver2.pk}" in keys
        assert f"approval_flow_pending_count_{bystander.pk}" not in keys
        assert pending_count_for(approver2) == 0

    def test_cancel_clears_pending_assignees_only(self, applicant, approver, approver2, monkeypatch):
        """撤回：失效集 = 被作废待办的 assignee（pending 列表一次性给出，无需全量）。"""
        bystander = UserInfo.objects.create_user(username="flow_bystander3", password="Test@123456")
        flow = make_flow(nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="撤回失效", form_data={})
        assert error is None, error
        cleared = self._record_cleared(monkeypatch)

        ok, detail = cancel_instance(instance, applicant)
        assert ok, detail
        keys = set(cleared)
        assert keys == {
            f"approval_flow_pending_count_{approver.pk}",
            f"approval_flow_pending_count_{approver2.pk}",
        }
        assert f"approval_flow_pending_count_{bystander.pk}" not in keys

    def test_cancel_pending_tasks_returns_assignees(self, applicant, approver, approver2):
        """作废任务返回 assignee pk 列表（计数失效集；assignee 为空的任务不计入）。"""
        from approval.utils.approval_flow.engine import _cancel_pending_tasks

        flow = make_flow(nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="返回值", form_data={})
        assert error is None, error
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=instance.current_node,
            node_name="审计节点",
            node_order=99,
            assignee=None,
            status=ApprovalNodeTask.Status.PENDING,
        )
        pks = _cancel_pending_tasks(instance)
        assert sorted(pks) == sorted([approver.pk, approver2.pk])

    def test_no_full_scan_invalidation_call_left(self):
        """源码级守护：全量失效（无参调用）已移除，防止回退。"""
        import inspect

        from approval.utils.approval_flow import engine, extra_actions

        for module in (engine, extra_actions):
            assert "_invalidate_pending_count()" not in inspect.getsource(module)


class TestBranchRoutes:
    """二期条件分支：排他网关出口路由 + 线性回退。"""

    def test_route_hit_jumps_to_target(self, applicant, approver):
        """路由命中跳转 target（排他网关）：跳过中间线性节点。"""
        from approval.utils.approval_flow import simulate_path

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
        from approval.utils.approval_flow import next_node

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
        # 文案断言必须 gettext 同源（有 .mo 显中文、无 .mo 显英文，写死英文会随语言环境失效）
        assert _gettext("The flow routes contain a loop, please contact the administrator") in str(error)


class TestRatioApprove:
    """二期比例会签：达标通过 / 不可能达标提前驳回。"""

    def _three_member_flow(self, applicant, approver, approver2, ratio):
        from tests.unit.approval.test_approval_flow import make_flow as _make

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
    """版本管理：保存落快照 / 回滚写回 / 改版解锁（在途实例按自身版本推进）。"""

    #: 节点审批人配置（role / flow_approver：与 make_flow 默认一致，实例可正常发起）
    NODE_CORE = {"assignee_type": "role", "assignee_value": "flow_approver"}

    def _flow_with_nodes(self, code):
        """经序列化器创建流程（v1 + 初始快照 + 生效节点行），与 UI 保存同口径。"""
        serializer = ApprovalFlowSerializer()
        flow = serializer.create(
            {
                "name": f"流程-{code}",
                "code": code,
                "nodes": [{"name": "节点一", "order": 1, **self.NODE_CORE}],
            }
        )
        return flow, serializer

    def test_rollback_writes_snapshot_back(self, applicant):

        flow, serializer = self._flow_with_nodes("ver_rollback")
        # 变更定义（加节点）→ v2：旧行收口 + 新版本落行（不物理删除）
        nodes_v2 = [{"name": "节点一", "order": 1, **self.NODE_CORE}, {"name": "节点二", "order": 2, **self.NODE_CORE}]
        flow = serializer.update(flow, {"nodes": nodes_v2})
        assert flow.versions.count() == 2
        assert flow.version == 2
        assert flow.nodes.count() == 2  # 当前生效行
        assert ApprovalFlowNode.all_objects.filter(flow=flow).count() == 3  # v1 行收口留档

        # 回滚到 v1 → 生效定义恢复单节点 + 落 v3（回滚也是一次变更）
        ok, detail = serializer.rollback_to_version(flow, 1)
        assert ok is True
        assert not detail
        assert flow.nodes.count() == 1
        assert flow.versions.count() == 3
        assert flow.versions.order_by("-version").first().version == 3

    def test_rollback_missing_version(self, applicant):

        flow, serializer = self._flow_with_nodes("ver_missing")
        ok, _detail = serializer.rollback_to_version(flow, 99)
        assert ok is False

    def test_rollback_allowed_with_pending_instance(self, applicant, approver):
        """改版解锁：有在途实例时回滚允许；在途单按自身版本走完，不受回滚影响。"""

        flow, serializer = self._flow_with_nodes("ver_pending")
        instance, error = create_instance(flow=flow, applicant=applicant, title="在途", form_data={})
        assert error is None
        ok, detail = serializer.rollback_to_version(flow, 1)
        assert ok is True
        assert not detail

        # 旧单仍可完成（v1 只有首节点，通过即终态）
        task = instance.tasks.get(status=ApprovalNodeTask.Status.PENDING)
        ok, _detail = approve_task(task.pk, approver)
        assert ok is True
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_create_instance_records_flow_version(self, applicant, approver):
        flow, _serializer = self._flow_with_nodes("ver_record")
        instance, error = create_instance(flow=flow, applicant=applicant, title="版本", form_data={})
        assert error is None
        assert instance.flow_version == flow.version


class TestConcurrencyGuard:
    """并发安全回归：重复推进不产生重复任务组、重复终态不重复投递副作用。

    真并发（线程/多进程）在 sqlite 测试库上不可靠（表级锁），这里用「同一操作重复
    执行」验证 CAS 语义——它正是并发交错时两个请求各自看到的状态（都认为自己是
    首个推进者）。生产环境的真正互斥由实例行锁（select_for_update）保证。
    """

    def test_repeated_approve_does_not_duplicate_next_node_tasks(self, applicant, approver, approver2):
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
        instance, error = create_instance(flow=flow, applicant=applicant, title="重复推进", form_data={})
        assert error is None
        task = instance.tasks.get(node_order=1, assignee=approver, status=ApprovalNodeTask.Status.PENDING)
        ok, detail = approve_task(task.pk, approver, "")
        assert ok, detail
        baseline = instance.tasks.filter(node_order=2).count()
        assert baseline == 1

        # 重复提交同一任务（并发交错时两个请求都会认为自己持有 PENDING 任务）
        ok, _detail = approve_task(task.pk, approver, "")
        assert not ok
        assert instance.tasks.filter(node_order=2).count() == baseline

    def test_repeated_reject_and_cancel_are_idempotent(self, applicant, approver):
        """驳回后再撤回：第二次操作被终态拦截，实例状态与时间戳不被覆盖。"""
        flow = make_flow(nodes=[{"name": "初审"}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="驳回幂等", form_data={})
        assert error is None
        task = instance.tasks.get(node_order=1, assignee=approver, status=ApprovalNodeTask.Status.PENDING)
        ok, detail = reject_task(task.pk, approver, "不同意")
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.REJECTED
        finished_at = instance.finished_at

        ok, _detail = cancel_instance(instance, applicant)
        assert not ok
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.REJECTED
        assert instance.finished_at == finished_at

    def test_finish_instance_cas_only_once(self, applicant, approver):
        """终态 CAS：重复置终态只生效一次（Webhook/业务回调/通知不重复投递）。"""
        from approval.utils.approval_flow.engine import _finish_instance

        flow = make_flow(nodes=[{"name": "初审"}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="终态幂等", form_data={})
        assert error is None
        assert _finish_instance(instance, ApprovalInstance.Status.APPROVED) is True
        assert _finish_instance(instance, ApprovalInstance.Status.REJECTED) is False
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED  # 第二次不得覆盖终态
        assert instance.finished_at is not None


class TestCreateAtomicityAndStuckCleanup:
    """发起事务化（不留卡死单）+ 卡死单兜底清理。"""

    def test_create_instance_rolls_back_on_failure(self, applicant, approver, monkeypatch):
        """发起中途异常：实例整体回滚，不留「PENDING 但无任何节点任务」的卡死单。"""
        flow = make_flow(nodes=[{"name": "初审"}])

        def boom(*args, **kwargs):
            raise RuntimeError("enter node failed")

        monkeypatch.setattr("approval.utils.approval_flow.engine._enter_node", boom)
        with pytest.raises(RuntimeError):
            create_instance(flow=flow, applicant=applicant, title="发起失败", form_data={})
        assert not ApprovalInstance.objects.filter(flow=flow).exists()
        assert not ApprovalNodeTask.objects.filter(instance__flow=flow).exists()

    def test_cancel_stuck_instances(self, applicant, approver):
        """超时且无任务的在途实例被兜底 CANCELLED；有任务的在途实例不受影响。"""
        from approval.utils.approval_flow import cancel_stuck_instances
        from approval.utils.approval_flow.periodic import STUCK_INSTANCE_TIMEOUT_MINUTES

        flow = make_flow(nodes=[{"name": "初审"}])
        stuck = ApprovalInstance.objects.create(
            flow=flow,
            flow_name=flow.name,
            title="卡死单",
            creator=applicant,
            current_node=None,
            flow_version=flow.version,
        )
        ApprovalInstance.objects.filter(pk=stuck.pk).update(
            created_time=timezone.now() - datetime.timedelta(minutes=STUCK_INSTANCE_TIMEOUT_MINUTES + 5)
        )
        healthy, error = create_instance(flow=flow, applicant=applicant, title="正常单", form_data={})
        assert error is None

        assert cancel_stuck_instances() == 1
        stuck.refresh_from_db()
        assert stuck.status == ApprovalInstance.Status.CANCELLED
        assert stuck.finished_at is not None
        healthy.refresh_from_db()
        assert healthy.status == ApprovalInstance.Status.PENDING

        # 幂等：重复执行不重复处理
        assert cancel_stuck_instances() == 0

    def test_fresh_pending_instance_not_cancelled(self, applicant, approver):
        """门槛内的无任务实例不动（避免误伤正在发起的请求）。"""
        from approval.utils.approval_flow import cancel_stuck_instances

        flow = make_flow(nodes=[{"name": "初审"}])
        fresh = ApprovalInstance.objects.create(
            flow=flow,
            flow_name=flow.name,
            title="刚创建",
            creator=applicant,
            flow_version=flow.version,
        )
        assert cancel_stuck_instances() == 0
        fresh.refresh_from_db()
        assert fresh.status == ApprovalInstance.Status.PENDING


class TestNotifyDeferredToCommit:
    """流程通知入队延迟到事务提交后（回滚不留幻影通知）。

    用 `django_db(transaction=True)` 走真实事务：非事务档下 pytest-django 会把测试包在
    atomic 里，提交语义不可观测（回调永不执行），无法区分「入队」与「立即投递」。
    """

    @staticmethod
    def _record(monkeypatch):
        events = []

        class _Recorder:
            def __init__(self, user, event, instance, extra=None, node_name=None):
                events.append(event)

            def publish(self, **kwargs):
                pass

        monkeypatch.setattr("system.notifications.ApprovalFlowMessage", _Recorder)
        return events

    @staticmethod
    def _user():
        return type("U", (), {"pk": 1})()

    @staticmethod
    def _instance():
        return type("I", (), {"pk": "x"})()

    @pytest.mark.django_db(transaction=True)
    def test_notify_immediate_without_transaction(self, monkeypatch):
        """无活动事务：提交后立即投递（语义与改造前一致，不改变既有调用方观感）。"""
        from approval.utils.approval_flow.engine import _notify

        events = self._record(monkeypatch)
        _notify([self._user()], "submitted", self._instance())
        assert events == ["submitted"]

    @pytest.mark.django_db(transaction=True)
    def test_notify_deferred_until_commit(self, monkeypatch):
        from django.db import transaction

        from approval.utils.approval_flow.engine import _notify

        events = self._record(monkeypatch)
        with transaction.atomic():
            _notify([self._user()], "approved", self._instance())
            # 事务内不入队投递（提交前发布会在回滚时留下幻影通知）
            assert events == []
        assert events == ["approved"]

    @pytest.mark.django_db(transaction=True)
    def test_notify_dropped_when_transaction_rolls_back(self, monkeypatch):
        """核心守卫：推进失败回滚后不得投递通知（否则点进去 404）。"""
        from django.db import transaction

        from approval.utils.approval_flow.engine import _notify

        events = self._record(monkeypatch)
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                _notify([self._user()], "submitted", self._instance())
                raise RuntimeError("推进失败")
        assert events == []

    def test_notify_skips_empty_users(self, monkeypatch, django_capture_on_commit_callbacks):
        from approval.utils.approval_flow.engine import _notify

        events = self._record(monkeypatch)
        with django_capture_on_commit_callbacks(execute=True):
            _notify([None, self._user()], "cc", self._instance())
        assert events == ["cc"]


class TestNodeLimitAndBatchPaths:
    """节点数量上限、一次取数推进与批量建任务（防畸形大图与 N+1）。"""

    @staticmethod
    def _nodes(count):
        return [
            {"name": f"节点{i}", "order": i + 1, "assignee_type": "role", "assignee_value": "flow_approver"}
            for i in range(count)
        ]

    def test_node_count_limit(self):
        """节点数上限：恰好上限通过，超过上限在保存时被拒。"""
        from approval.utils.approval_flow import MAX_FLOW_NODES

        ok = ApprovalFlowSerializer(data={"name": "上限流程", "code": "limit_ok", "nodes": self._nodes(MAX_FLOW_NODES)})
        assert ok.is_valid(), ok.errors

        over = ApprovalFlowSerializer(
            data={"name": "超限流程", "code": "limit_over", "nodes": self._nodes(MAX_FLOW_NODES + 1)}
        )
        assert not over.is_valid()
        assert str(MAX_FLOW_NODES) in str(over.errors)

    def test_simulate_path_reads_nodes_once(self):
        """模拟推进一次取数后内存推进：查询数不随流程长度增长（改造前逐步查库）。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from approval.utils.approval_flow import simulate_path

        def node_queries(count):
            flow = make_flow(
                code=f"sim_flow_{count}", nodes=[{"name": f"节点{i}", "order": i} for i in range(1, count + 1)]
            )
            with CaptureQueriesContext(connection) as ctx:
                path = simulate_path(flow, {})
            assert len(path) == count
            return [item for item in ctx.captured_queries if "approval_approvalflownode" in item["sql"].lower()]

        assert len(node_queries(3)) == len(node_queries(8)) == 1

    def test_enter_node_bulk_creates_candidate_tasks(self, applicant, approver, approver2):
        """多候选一次 INSERT，任务行（含显示名快照）语义不变。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from approval.utils.approval_flow.engine import _enter_node

        flow = make_flow(code="bulk_flow", nodes=[{"name": "会签", "order": 1, "assignee_value": "flow_approver"}])
        node = flow.nodes.get(order=1)
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="批量建任务", creator=applicant, flow_version=flow.version
        )
        with CaptureQueriesContext(connection) as ctx:
            assert _enter_node(instance, node) is True
        inserts = [
            item
            for item in ctx.captured_queries
            if item["sql"].strip().lower().startswith("insert into")
            and "approval_approvalnodetask" in item["sql"].lower()
        ]
        assert len(inserts) == 1
        tasks = list(ApprovalNodeTask.objects.filter(instance=instance))
        assert {task.assignee_id for task in tasks} == {approver.pk, approver2.pk}
        assert all(task.assignee_display for task in tasks)
        assert all(task.status == ApprovalNodeTask.Status.PENDING for task in tasks)

    def test_instance_cc_single_query(self, applicant, approver, approver2):
        """抄送标识（pk 与用户名混合）一次批量解析，逐标识保持「主键优先」语义。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from approval.utils.approval_flow.assignees import _resolve_instance_cc

        with CaptureQueriesContext(connection) as ctx:
            users = _resolve_instance_cc([], applicant, [str(approver.pk), approver2.username])
        assert {user.pk for user in users} == {approver.pk, approver2.pk}
        assert len([item for item in ctx.captured_queries if "system_userinfo" in item["sql"].lower()]) == 1

    def test_auto_approved_alerts_webhook_and_admins(
        self, monkeypatch, applicant, superuser, django_capture_on_commit_callbacks
    ):
        """无候选自动通过：审计行照旧 + 出站 Webhook + 知会超管（节点名不串上一节点）。"""
        from approval.utils.approval_flow.engine import _enter_node

        flow = make_flow(
            code="no_candidate_flow", nodes=[{"name": "空节点", "order": 1, "assignee_value": "ghost_role"}]
        )
        node = flow.nodes.get(order=1)
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="无候选", creator=applicant, flow_version=flow.version
        )

        emitted = []
        monkeypatch.setattr(
            "system.utils.webhook.emit_webhook_event", lambda event, data: emitted.append((event, data))
        )
        notified = []

        class _Recorder:
            def __init__(self, user, event, instance, extra=None, node_name=None):
                notified.append({"user": user.pk, "event": event, "node_name": node_name})

            def publish(self, **kwargs):
                pass

        monkeypatch.setattr("system.notifications.ApprovalFlowMessage", _Recorder)

        # 通知经 transaction.on_commit 入队：测试事务内需显式执行回调
        with django_capture_on_commit_callbacks(execute=True):
            assert _enter_node(instance, node) is False

        assert [event for event, _data in emitted] == ["flow.node_auto_approved"]
        assert emitted[0][1]["node_name"] == "空节点"
        assert [item["event"] for item in notified] == ["auto_approved"]
        assert notified[0]["node_name"] == "空节点"
        assert notified[0]["user"] == superuser.pk

        task = ApprovalNodeTask.objects.get(instance=instance, node=node)
        assert task.status == ApprovalNodeTask.Status.APPROVED
        assert task.assignee is None
