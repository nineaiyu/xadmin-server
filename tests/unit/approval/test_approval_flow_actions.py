# -*- coding: utf-8 -*-
"""审批流 P2 三件：超时自动动作（timeout_action）/ 退回指定节点 / 减签。

覆盖口径（REFACTORING-PLAN §7.1-2/3/4）：
- 超时自动动作：approve（系统代通过，节点结算与人工同口径）/ reject（整单终态）/
  transfer_up（升级给处理人部门 leader，无 leader 节流跳过）；
- 退回：仅已途经节点、保持 PENDING 重开目标节点、无候选 fail-closed；
- 减签：仅 is_added 行、OR 节点拒绝、审计行保留。
"""

import datetime

import pytest
from django.utils import timezone

from approval.models.approval import ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from approval.utils.approval_flow import (
    add_sign,
    approve_task,
    create_instance,
    execute_timeout_actions,
    reject_task,
    remove_sign,
    return_instance,
    returnable_nodes,
)
from system.models import DeptInfo, UserInfo, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver_role(db):
    return UserRole.objects.create(name="审批人", code="flow2_approver")


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="flow2_applicant", password="Test@123456", nickname="申请人")


@pytest.fixture
def approver(approver_role):
    user = UserInfo.objects.create_user(username="flow2_approver1", password="Test@123456", nickname="审批人一")
    user.roles.add(approver_role)
    return user


@pytest.fixture
def approver2(approver_role):
    user = UserInfo.objects.create_user(username="flow2_approver2", password="Test@123456", nickname="审批人二")
    user.roles.add(approver_role)
    return user


@pytest.fixture
def dept_leader(db):
    return UserInfo.objects.create_user(username="flow2_leader", password="Test@123456", nickname="部门负责人")


@pytest.fixture
def dept(dept_leader, approver):
    return DeptInfo.objects.create(name="审批一部", code="flow2_dept", leader=dept_leader)


def _backdate_tasks(instance, hours=2):
    """把实例全部任务的创建时间前移 N 小时（越过节点超时线）。"""
    old = timezone.now() - datetime.timedelta(hours=hours)
    ApprovalNodeTask.objects.filter(instance=instance).update(created_time=old)


def make_flow(code="flow2", nodes=None):
    flow_nodes = nodes or [{"name": "初审"}]
    flow = ApprovalFlowNode._meta.get_field("flow").remote_field.model.objects.create(
        name=f"流程-{code}", code=code, form_schema=[], is_active=True
    )
    for index, node in enumerate(flow_nodes):
        params = dict(
            name=node.get("name") or f"节点{index + 1}",
            order=node.get("order") or index + 1,
            approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
            approve_ratio=node.get("approve_ratio", 100),
            assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
            assignee_value=node.get("assignee_value", "flow2_approver"),
            condition=node.get("condition") or {},
            routes=node.get("routes") or [],
            timeout_hours=node.get("timeout_hours") or 0,
            timeout_action=node.get("timeout_action") or ApprovalFlowNode.TimeoutAction.NONE,
        )
        ApprovalFlowNode.objects.create(flow=flow, **params)
    return flow


# ---------------------------------------------------------------- 超时自动动作


class TestTimeoutAutoActions:
    def test_none_action_only_reminds(self, applicant, approver):
        """timeout_action=none（默认）：到点也不代处理，行为与改造前一致。"""
        flow = make_flow(nodes=[{"name": "初审", "timeout_hours": 1}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert error is None
        _backdate_tasks(instance)
        assert execute_timeout_actions() == {"approve": 0, "reject": 0, "transfer_up": 0}
        task = instance.tasks.get(status=ApprovalNodeTask.Status.PENDING)
        assert task.status == ApprovalNodeTask.Status.PENDING
        assert task.actor is None

    def test_not_timed_out_no_action(self, applicant, approver):
        """未到超时线：不动作。"""
        flow = make_flow(nodes=[{"name": "初审", "timeout_hours": 8, "timeout_action": "approve"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert execute_timeout_actions() == {"approve": 0, "reject": 0, "transfer_up": 0}
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).exists()

    def test_auto_approve_advances_or_node(self, applicant, approver, approver2):
        """或签节点自动通过：任务置 APPROVED（系统代处理留痕）并推进。"""
        flow = make_flow(
            nodes=[
                {"name": "初审", "timeout_hours": 1, "timeout_action": "approve"},
                {
                    "name": "终审",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                },
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        counts = execute_timeout_actions()
        assert counts["approve"] == 1
        instance.refresh_from_db()
        # 或签通过即流转：终审任务已建
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 2
        acted = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.APPROVED)
        assert acted.actor is None  # 系统代处理：无 actor
        assert acted.acted_at is not None
        assert acted.comment  # 注明超时自动通过

    def test_auto_approve_and_node_waits_for_others(self, applicant, approver, approver2):
        """会签节点自动通过一人：其余候选人仍需处理，不整单放行。"""
        flow = make_flow(
            nodes=[
                {
                    "name": "会签",
                    "timeout_hours": 1,
                    "timeout_action": "approve",
                    "approve_type": ApprovalFlowNode.ApproveType.AND,
                }
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        assert execute_timeout_actions()["approve"] == 2
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED  # 两人都超时 → 都代通过后齐票

    def test_auto_approve_ratio_node_reaches_threshold(self, applicant, approver, approver2):
        """比例会签：自动通过达到达标线即推进（50% × 2 人 = 1 票）。"""
        flow = make_flow(
            nodes=[
                {
                    "name": "比例会签",
                    "timeout_hours": 1,
                    "timeout_action": "approve",
                    "approve_type": ApprovalFlowNode.ApproveType.RATIO,
                    "approve_ratio": 50,
                }
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        counts = execute_timeout_actions()
        # 首票代通过即达标 → 其余待办作废，第二个任务不再触发 approve
        assert counts["approve"] == 1
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_auto_reject_finishes_instance(self, applicant, approver, approver2):
        """超时自动驳回：整单终态 REJECTED，原因与轨迹注明系统代处理。"""
        flow = make_flow(nodes=[{"name": "初审", "timeout_hours": 1, "timeout_action": "reject"}, {"name": "终审"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        counts = execute_timeout_actions()
        assert counts["reject"] == 1
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.REJECTED
        assert instance.finished_at is not None
        assert instance.current_node is None
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 0
        rejected = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.REJECTED)
        assert rejected.actor is None

    def test_auto_transfer_up_to_dept_leader(self, applicant, dept, approver):
        """升级转交：原任务作废（留痕），新任务 assignee=部门 leader、delegate_from=原处理人。"""
        approver.dept = dept
        approver.save(update_fields=["dept"])
        flow = make_flow(
            nodes=[
                {
                    "name": "初审",
                    "timeout_hours": 1,
                    "timeout_action": "transfer_up",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                }
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        counts = execute_timeout_actions()
        assert counts["transfer_up"] == 1
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        original = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.CANCELLED)
        assert original.comment
        escalated = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.PENDING)
        assert escalated.assignee_id == dept.leader_id
        assert escalated.delegate_from_id == approver.pk

    def test_auto_transfer_up_without_leader_skips_with_throttle(self, applicant, approver):
        """无部门 leader：跳过且 24h 内不重复尝试（任务保持 PENDING 由提醒链路兜底）。"""
        flow = make_flow(
            nodes=[
                {
                    "name": "初审",
                    "timeout_hours": 1,
                    "timeout_action": "transfer_up",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                }
            ]
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        _backdate_tasks(instance)
        assert execute_timeout_actions()["transfer_up"] == 0
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING, assignee=approver).exists()
        # 第二轮：节流窗口内不再产生日志/尝试，任务仍 PENDING
        assert execute_timeout_actions()["transfer_up"] == 0
        assert instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 1

    def test_finished_instance_not_touched(self, applicant, approver):
        """人工已处理的任务/已终结的实例：超时动作不重复触发（行锁内 CAS 兜底）。"""
        flow = make_flow(nodes=[{"name": "初审", "timeout_hours": 1, "timeout_action": "approve"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        task = instance.tasks.get(status=ApprovalNodeTask.Status.PENDING)
        ok, _ = approve_task(task.pk, approver, "人工先处理")
        assert ok
        _backdate_tasks(instance)
        assert execute_timeout_actions() == {"approve": 0, "reject": 0, "transfer_up": 0}
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED


# ---------------------------------------------------------------- 退回指定节点


class TestReturnToNode:
    def _two_node_instance(self, applicant):
        flow = make_flow(code="flow2_ret", nodes=[{"name": "初审"}, {"name": "终审"}])
        instance, error = create_instance(flow=flow, applicant=applicant, title="退回申请", form_data={})
        assert error is None
        return instance

    def test_returnable_nodes_lists_visited(self, applicant, approver):
        instance = self._two_node_instance(applicant)
        assert returnable_nodes(instance) == []
        task = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.PENDING)
        ok, _ = approve_task(task.pk, approver, "同意")
        assert ok
        instance.refresh_from_db()
        rows = returnable_nodes(instance)
        assert [row["order"] for row in rows] == [1]
        assert rows[0]["name"] == "初审"

    def test_return_to_previous_reopens_node(self, applicant, approver, approver2):
        """缺省退回上一途经节点：当前待办作废、目标节点重开、实例保持 PENDING。"""
        instance = self._two_node_instance(applicant)
        ok, _ = approve_task(
            instance.tasks.get(node_order=1, assignee=approver, status=ApprovalNodeTask.Status.PENDING).pk, approver
        )
        assert ok
        instance.refresh_from_db()
        ok, detail = return_instance(instance, approver, "材料不全，退回补充")
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 1
        # 退回原因落在被作废的任务轨迹上
        cancelled = instance.tasks.filter(node_order=2, status=ApprovalNodeTask.Status.CANCELLED)
        assert cancelled.count() == 2  # 或签节点：处理人通过后其余作废 + 退回整批作废
        assert any("材料不全" in (row.comment or "") for row in cancelled)
        # 目标节点重开待办（初审是角色节点 → 两名候选）
        reopened = instance.tasks.filter(node_order=1, status=ApprovalNodeTask.Status.PENDING)
        assert reopened.count() == 2

    def test_return_to_specified_visited_node(self, applicant, approver, approver2):
        """显式指定已途经节点：按 order 精确退回。"""
        flow = make_flow(code="flow2_ret3", nodes=[{"name": "初审"}, {"name": "复核"}, {"name": "终审"}])
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        for order, user in ((1, approver), (2, approver2)):
            task = instance.tasks.get(node_order=order, assignee=user, status=ApprovalNodeTask.Status.PENDING)
            ok, _ = approve_task(task.pk, user)
            assert ok
        instance.refresh_from_db()
        ok, detail = return_instance(instance, approver2, "复核有误", target_order=1)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.current_node.order == 1

    def test_return_rejects_unvisited_or_future_target(self, applicant, approver):
        instance = self._two_node_instance(applicant)
        # 未途经 / 大于当前节点 / 非法值 → 一律拒绝
        ok, detail = return_instance(instance, approver, "x", target_order=2)
        assert not ok
        ok, detail = return_instance(instance, approver, "x", target_order=99)
        assert not ok
        ok, detail = return_instance(instance, approver, "x", target_order="abc")
        assert not ok
        instance.refresh_from_db()
        assert instance.current_node.order == 1

    def test_return_first_node_has_no_target(self, applicant, approver):
        instance = self._two_node_instance(applicant)
        ok, detail = return_instance(instance, approver, "x")
        assert not ok
        assert "earlier" in detail or "更早" in detail

    def test_return_reason_required_and_permission(self, applicant, approver, approver2, superuser):
        instance = self._two_node_instance(applicant)
        ok, _ = approve_task(
            instance.tasks.get(node_order=1, assignee=approver, status=ApprovalNodeTask.Status.PENDING).pk, approver
        )
        instance.refresh_from_db()
        # 原因必填
        ok, detail = return_instance(instance, approver2, "")
        assert not ok
        # 非当前节点参与人不能退回（approver2 已通过 → 是参与者；换无关用户）
        outsider = UserInfo.objects.create_user(username="flow2_outsider", password="Test@123456")
        ok, detail = return_instance(instance, outsider, "x")
        assert not ok
        # 超管放行
        ok, detail = return_instance(instance, superuser, "管理员清障")
        assert ok, detail

    def test_return_no_candidates_fail_closed(self, applicant, approver):
        """目标节点候选已消失：写入前拒绝，不产生卡死单。"""
        flow = make_flow(
            code="flow2_ret_nc",
            nodes=[
                {
                    "name": "初审",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                },
                {"name": "终审"},
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        ok, _ = approve_task(instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.PENDING).pk, approver)
        instance.refresh_from_db()
        # 初审唯一候选人停用 → 重开必然无候选
        approver.is_active = False
        approver.save(update_fields=["is_active"])
        before_tasks = instance.tasks.count()
        ok, detail = return_instance(instance, UserInfo.objects.get(username="flow2_approver1"), "x")
        assert not ok
        instance.refresh_from_db()
        assert instance.tasks.count() == before_tasks  # 无任何写入
        assert instance.current_node.order == 2

    def test_return_then_reapprove_advances_again(self, applicant, approver):
        """退回后重新审批：目标节点走完后正常向前流转（终态可达）。"""
        instance = self._two_node_instance(applicant)
        ok, _ = approve_task(instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.PENDING).pk, approver)
        instance.refresh_from_db()
        ok, detail = return_instance(instance, approver, "补充材料后重审")
        assert ok, detail
        reopened = instance.tasks.get(node_order=1, status=ApprovalNodeTask.Status.PENDING, assignee=approver)
        ok, detail = approve_task(reopened.pk, approver, "重新通过")
        assert ok, detail
        ok, detail = approve_task(
            instance.tasks.filter(node_order=2, status=ApprovalNodeTask.Status.PENDING, assignee=approver).first().pk,
            approver,
            "",
        )
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED


# ---------------------------------------------------------------- 减签


class TestRemoveSign:
    def _and_instance_with_addsign(self, applicant, approver, approver2):
        flow = make_flow(
            code="flow2_rm",
            nodes=[
                {
                    "name": "会签",
                    "approve_type": ApprovalFlowNode.ApproveType.AND,
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                }
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        ok, detail = add_sign(instance, approver, "flow2_approver2", "请协助")
        assert ok, detail
        return instance

    def test_remove_sign_cancels_added_task(self, applicant, approver, approver2):
        """减签：加签行作废（审计保留），剩余会签候选通过即流转。"""
        instance = self._and_instance_with_addsign(applicant, approver, approver2)
        added = instance.tasks.get(node=instance.current_node, assignee=approver2, is_added=True)
        ok, detail = remove_sign(instance, approver, added.pk, "不需要会审了")
        assert ok, detail
        added.refresh_from_db()
        assert added.status == ApprovalNodeTask.Status.CANCELLED
        assert "不需要会审了" in (added.comment or "")
        # 剩余候选通过 → 实例通过（减签降低了会签所需人数）
        ok, detail = approve_task(
            instance.tasks.get(
                node=instance.current_node, assignee=approver, status=ApprovalNodeTask.Status.PENDING
            ).pk,
            approver,
        )
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_remove_sign_rejects_definition_task(self, applicant, approver, approver2):
        """流程定义解析出的初始候选不能减签（换人走转交）。"""
        instance = self._and_instance_with_addsign(applicant, approver, approver2)
        original = instance.tasks.get(
            node=instance.current_node, assignee=approver, is_added=False, status=ApprovalNodeTask.Status.PENDING
        )
        ok, detail = remove_sign(instance, approver, original.pk)
        assert not ok

    def test_remove_sign_or_node_rejected(self, applicant, approver, approver2):
        """或签节点：减签与加签同口径拒绝并引导转交。"""
        flow = make_flow(
            code="flow2_rm_or",
            nodes=[
                {
                    "name": "或签",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                }
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        ok, detail = add_sign(instance, approver, "flow2_approver2")
        assert not ok  # 或签下加签本就被拒
        task = instance.tasks.get(assignee=approver, status=ApprovalNodeTask.Status.PENDING)
        ok, detail = remove_sign(instance, approver, task.pk)
        assert not ok
        assert "transfer" in detail or "转交" in detail

    def test_remove_sign_permission_and_processed(self, applicant, approver, approver2):
        instance = self._and_instance_with_addsign(applicant, approver, approver2)
        added = instance.tasks.get(node=instance.current_node, assignee=approver2, is_added=True)
        # 无关用户不能减签
        outsider = UserInfo.objects.create_user(username="flow2_outsider2", password="Test@123456")
        ok, detail = remove_sign(instance, outsider, added.pk)
        assert not ok
        # 已处理的任务不能减签
        ApprovalNodeTask.objects.filter(pk=added.pk).update(status=ApprovalNodeTask.Status.APPROVED)
        ok, detail = remove_sign(instance, approver, added.pk)
        assert not ok
        # 驳回整单后不能再减签
        other = make_flow(code="flow2_rm2", nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}])
        instance2, _ = create_instance(flow=other, applicant=applicant, title="x", form_data={})
        reject_task(instance2.tasks.first().pk, approver, "不通过")
        added2 = instance2.tasks.filter(is_added=True).first()
        if added2:
            ok, detail = remove_sign(instance2, approver, added2.pk)
            assert not ok


class TestRemoveSignRatio:
    def test_remove_sign_lowers_ratio_threshold(self, applicant, approver, approver2, dept_leader):
        """减签降低比例会签达标线：已作废行不计入候选总数（与 engine 判定同口径）。

        ratio=100：2 名初始候选需 2 票 → 加签后 3 票 → 减签移除加签行后有效候选 2 →
        仍需 2 票（旧口径下已作废行计入总数，达标线会停留在 3 票永不达标）。
        """
        flow = make_flow(
            code="flow2_rm_ratio",
            nodes=[
                {
                    "name": "比例会签",
                    "approve_type": ApprovalFlowNode.ApproveType.RATIO,
                    "approve_ratio": 100,
                }
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        from approval.utils.approval_flow import node_progress_for

        progress = node_progress_for(instance)
        assert progress["total"] == 2
        assert progress["required"] == 2

        ok, detail = add_sign(instance, approver, "flow2_leader", "增援")
        assert ok, detail
        progress = node_progress_for(instance)
        assert progress["total"] == 3
        assert progress["required"] == 3

        added = instance.tasks.get(node=instance.current_node, assignee=dept_leader, is_added=True)
        ok, detail = remove_sign(instance, approver, added.pk)
        assert ok, detail
        progress = node_progress_for(instance)
        assert progress["total"] == 2  # 已作废行不再计入
        assert progress["required"] == 2

        # 剩余两票通过即达标（旧口径下 required=3 永不达标）
        for user in (approver, approver2):
            ok, detail = approve_task(
                instance.tasks.get(
                    node=instance.current_node, assignee=user, status=ApprovalNodeTask.Status.PENDING
                ).pk,
                user,
            )
            assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
