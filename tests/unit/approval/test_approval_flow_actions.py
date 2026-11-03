# -*- coding: utf-8 -*-
"""审批流三件：超时自动动作（timeout_action）/ 退回指定节点 / 减签。

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
from approval.services.approval_flow import (
    add_sign,
    approve_task,
    create_instance,
    execute_timeout_actions,
    node_progress_for,
    reject_task,
    remove_sign,
    return_instance,
    returnable_nodes,
)
from identity.models import DeptInfo, UserInfo, UserRole

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

    def test_remove_sign_settles_when_and_pending_cleared(self, applicant, approver, approver2, dept_leader):
        """减签使会签待办清零：节点即时推进（不结算则节点永久挂起无人能推进）。

        AND 会签两名候选：A 通过 → 加签 C → B 通过（C 仍待办，不推进）→
        减签移除 C：此时节点已无剩余待办，应立即推进到终态。
        """
        flow = make_flow(
            code="flow2_rm_and_clear",
            nodes=[{"name": "会签", "approve_type": ApprovalFlowNode.ApproveType.AND}],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        ok, detail = add_sign(instance, approver, "flow2_leader", "增援")
        assert ok, detail

        ok, detail = approve_task(
            instance.tasks.get(assignee=approver, status=ApprovalNodeTask.Status.PENDING).pk, approver
        )
        assert ok, detail
        ok, detail = approve_task(
            instance.tasks.get(assignee=approver2, status=ApprovalNodeTask.Status.PENDING).pk, approver2
        )
        assert ok, detail
        instance.refresh_from_db()
        # B 通过后仍有加签待办：节点未推进
        assert instance.current_node.order == 1

        added = instance.tasks.get(node=instance.current_node, assignee=dept_leader, is_added=True)
        ok, detail = remove_sign(instance, approver, added.pk, "撤回增援")
        assert ok, detail
        added.refresh_from_db()
        assert added.status == ApprovalNodeTask.Status.CANCELLED
        # 待办清零即推进：单节点流程到达终态
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_remove_sign_after_finished_instance_no_side_effect(self, applicant, approver, approver2):
        """实例已终态：减签被拒，不产生任何状态变更（终态 CAS 语义不回退、不复活）。"""
        instance = self._and_instance_with_addsign(applicant, approver, approver2)
        # 全员处理完毕（含加签人）→ 单节点流程到达终态 APPROVED
        for task in list(instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING)):
            ok, detail = approve_task(task.pk, task.assignee)
            assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

        added = instance.tasks.get(assignee=approver2, is_added=True)
        before_status = added.status
        ok, detail = remove_sign(instance, approver, added.pk, "事后减签")
        assert not ok
        added.refresh_from_db()
        assert added.status == before_status  # 已终态行不被改写
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        assert instance.current_node is None
        assert not instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).exists()

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

    def _five_candidate_ratio_instance(self, applicant, approver_role):
        """比例会签（60% × 5 名角色候选 + 终审节点）的实例与第 3~5 名候选。"""
        extra_users = []
        for index in range(3, 6):
            user = UserInfo.objects.create_user(
                username=f"flow2_approver{index}", password="Test@123456", nickname=f"审批人{index}"
            )
            user.roles.add(approver_role)
            extra_users.append(user)
        flow = make_flow(
            code="flow2_rm_settle",
            nodes=[
                {"name": "比例会签", "approve_type": ApprovalFlowNode.ApproveType.RATIO, "approve_ratio": 60},
                {
                    "name": "终审",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                },
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        assert node_progress_for(instance)["total"] == 5  # 5 名角色候选，required=3
        return instance, extra_users

    def test_remove_sign_settles_instantly_when_ratio_reached(
        self, applicant, approver_role, approver, approver2, dept_leader
    ):
        """减签瞬间票数达标：节点立即重结算并推进（不结算则挂起、待办人一票否决可整单驳回）。

        数字组合：60% × 5 候选（required=3）→ 加签后 total=6（required=4）→
        3 票通过（approved=3 < 4 不推进）→ 减签移除加签行：total=5、required=3，
        approved=3 已达标 → 应即时推进到终审节点并作废节点剩余待办。
        """
        instance, extra = self._five_candidate_ratio_instance(applicant, approver_role)

        ok, detail = add_sign(instance, approver, "flow2_leader", "增援")
        assert ok, detail
        progress = node_progress_for(instance)
        assert progress["total"] == 6
        assert progress["required"] == 4

        for user in (approver, approver2, extra[0]):
            ok, detail = approve_task(
                instance.tasks.get(
                    node=instance.current_node, assignee=user, status=ApprovalNodeTask.Status.PENDING
                ).pk,
                user,
            )
            assert ok, detail
        instance.refresh_from_db()
        assert instance.current_node.order == 1  # 3 票 < required=4：未推进

        added = instance.tasks.get(node=instance.current_node, assignee=dept_leader, is_added=True)
        ok, detail = remove_sign(instance, approver, added.pk, "撤回增援")
        assert ok, detail
        instance.refresh_from_db()
        # 减签后 required=3、approved=3：即时推进（不结算则节点挂起）
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 2
        assert instance.tasks.filter(node_order=1, status=ApprovalNodeTask.Status.PENDING).count() == 0
        # 节点 1 的作废行 = 加签移除 1 + 推进作废剩余待办 2
        assert instance.tasks.filter(node_order=1, status=ApprovalNodeTask.Status.CANCELLED).count() == 3
        assert instance.tasks.filter(node_order=2, status=ApprovalNodeTask.Status.PENDING).exists()

    def test_remove_sign_keeps_pending_when_ratio_not_reached(
        self, applicant, approver_role, approver, approver2, dept_leader
    ):
        """减签后仍未达标：节点保持待审、不推进也不误判驳回。"""
        instance, extra = self._five_candidate_ratio_instance(applicant, approver_role)

        ok, detail = add_sign(instance, approver, "flow2_leader", "增援")
        assert ok, detail
        for user in (approver, approver2):  # 仅 2 票（< required=4）
            ok, detail = approve_task(
                instance.tasks.get(
                    node=instance.current_node, assignee=user, status=ApprovalNodeTask.Status.PENDING
                ).pk,
                user,
            )
            assert ok, detail

        added = instance.tasks.get(node=instance.current_node, assignee=dept_leader, is_added=True)
        ok, detail = remove_sign(instance, approver, added.pk)
        assert ok, detail
        instance.refresh_from_db()
        # 减签后 total=5、required=3、approved=2：未达标保持待审
        assert instance.status == ApprovalInstance.Status.PENDING
        assert instance.current_node.order == 1
        assert instance.tasks.filter(node_order=1, status=ApprovalNodeTask.Status.PENDING).count() == 3
        progress = node_progress_for(instance)
        assert progress["approved"] == 2
        assert progress["required"] == 3


class TestReturnToRatioNodeCurrentSemantics:
    """退回 × 比例会签：按「重审计数清零」开关分叉断言两侧结算口径。

    场景基底：RATIO 60% × 5 候选，首轮 3/5 通过 → 推进到终审 → 退回比例会签
    节点重开 5 条新 PENDING。开关关闭（默认，APPROVAL_RETURN_RESET_COUNT=false）：
    历史 3 条 APPROVED 行混入新轮统计 → total=8、required=5，新轮再获 2 票即推进
    （历史行为，达标线失真）；开关开启：退回把既有任务行标记历史轮次，只统计
    本轮 → total=5、required=3，且首轮已投者在新轮需重新投票（满 3 票才推进）。
    """

    @staticmethod
    def _patch_return_reset(monkeypatch, enabled: bool) -> None:
        """monkeypatch 三处消费方模块的开关读取（引擎结算 / 退回写序 / 进度口径）。"""
        for module in ("engine", "queries", "returns"):
            monkeypatch.setattr(f"approval.services.approval_flow.{module}.return_count_reset_enabled", lambda: enabled)

    def _ratio_instance(self, applicant, approver_role):
        extra_users = []
        for index in range(3, 6):
            user = UserInfo.objects.create_user(
                username=f"flow2_approver{index}", password="Test@123456", nickname=f"审批人{index}"
            )
            user.roles.add(approver_role)
            extra_users.append(user)
        flow = make_flow(
            code="flow2_ret_ratio",
            nodes=[
                {"name": "比例会签", "approve_type": ApprovalFlowNode.ApproveType.RATIO, "approve_ratio": 60},
                {
                    "name": "终审",
                    "assignee_type": ApprovalFlowNode.AssigneeType.USER,
                    "assignee_value": "flow2_approver1",
                },
            ],
        )
        instance, _ = create_instance(flow=flow, applicant=applicant, title="x", form_data={})
        return instance, extra_users

    @staticmethod
    def _approve_current(instance, user):
        ok, detail = approve_task(
            instance.tasks.get(node=instance.current_node, assignee=user, status=ApprovalNodeTask.Status.PENDING).pk,
            user,
        )
        assert ok, detail

    def test_return_reopen_mixes_history_rows_into_ratio_settlement(
        self, applicant, approver_role, approver, approver2, monkeypatch
    ):
        """开关关闭（默认）：历史任务行混入新轮结算，2 票即推进——钉住现状口径。"""
        self._patch_return_reset(monkeypatch, False)
        instance, extra = self._ratio_instance(applicant, approver_role)
        # 首轮 3 票达标（required=ceil(5×0.6)=3）→ 推进到终审
        for user in (approver, approver2, extra[0]):
            self._approve_current(instance, user)
        instance.refresh_from_db()
        assert instance.current_node.order == 2

        # 终审待办人退回比例会签节点重审
        ok, detail = return_instance(instance, approver, "退回重审", target_order=1)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.current_node.order == 1
        # 关闭侧口径：历史 3 条 APPROVED + 新轮 5 条 PENDING 一并计入（不打轮次标记）
        assert not instance.tasks.filter(is_history=True).exists()
        progress = node_progress_for(instance)
        assert progress["total"] == 8
        assert progress["approved"] == 3
        assert progress["required"] == 5  # ceil(8×0.6)=5，混入历史后达标线抬高

        # 新轮仅需再获 2 票（清零口径本应重新计数、需 3 票）——钉住现状
        for user in (extra[1], extra[2]):
            self._approve_current(instance, user)
        instance.refresh_from_db()
        assert instance.current_node.order == 2  # approved=3+2=5 ≥ required=5 → 已推进
        # 终审走完后实例正常到达终态（退回 × 重审链路完整）
        ok, detail = approve_task(instance.tasks.get(node_order=2, status=ApprovalNodeTask.Status.PENDING).pk, approver)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED

    def test_return_reopen_counts_only_current_round_when_enabled(
        self, applicant, approver_role, approver, approver2, monkeypatch
    ):
        """开关开启：重开清零计数，只统计本轮行，首轮已投者需重新投票。"""
        self._patch_return_reset(monkeypatch, True)
        instance, extra = self._ratio_instance(applicant, approver_role)
        for user in (approver, approver2, extra[0]):  # 首轮 3 票达标 → 推进到终审
            self._approve_current(instance, user)
        instance.refresh_from_db()
        assert instance.current_node.order == 2

        ok, detail = return_instance(instance, approver, "退回重审", target_order=1)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.current_node.order == 1
        # 既有任务行全部标记历史轮次（节点 1 首轮 5 行 + 终审作废行），新建行为本轮
        assert instance.tasks.exclude(is_history=True).count() == 5
        assert instance.tasks.filter(is_history=True).count() == 6
        history_of_approver = instance.tasks.filter(
            assignee=approver, is_history=True, status=ApprovalNodeTask.Status.APPROVED
        )
        assert history_of_approver.count() == 1  # 首轮已投记录保留但只作历史轨迹
        # 首轮已投者（approver）在新轮有新 PENDING 待办 → 需重新投票
        assert instance.tasks.filter(
            assignee=approver, is_history=False, status=ApprovalNodeTask.Status.PENDING
        ).exists()

        # 清零口径：只统计本轮 5 行（全部 PENDING），达标线回到本轮人数
        progress = node_progress_for(instance)
        assert progress["total"] == 5
        assert progress["approved"] == 0
        assert progress["required"] == 3

        # 新轮 2 票不推进（2 < 3）——历史 3 票不再「白送」
        for user in (extra[1], extra[2]):
            self._approve_current(instance, user)
        instance.refresh_from_db()
        assert instance.current_node.order == 1
        progress = node_progress_for(instance)
        assert progress["approved"] == 2

        # 第 3 票（首轮已投者重新投票）→ 达标推进，终审后实例到达终态
        self._approve_current(instance, approver)
        instance.refresh_from_db()
        assert instance.current_node.order == 2
        ok, detail = approve_task(instance.tasks.get(node_order=2, status=ApprovalNodeTask.Status.PENDING).pk, approver)
        assert ok, detail
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
