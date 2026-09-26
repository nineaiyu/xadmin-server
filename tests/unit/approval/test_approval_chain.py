# -*- coding: utf-8 -*-
"""敏感操作审批：多级审批链（审批规则 → 级次快照 → 逐级推进）。

覆盖：
- 规则匹配（优先级 / 停用跳过 / 未命中回退扁平单）；
- 建单快照（fail-closed：某级无可用人则不建单）+ 申请人剔除；
- 逐级推进（当前级投影切换 / 越级被拒 / 末级才落终态与令牌有效期）；
- 驳回终止（当前级 REJECTED、其余级 CANCELLED）、撤回/过期的级次清理；
- 待办口径（只有当前级候选人可见，与页签列表同源）。
"""

import datetime

import pytest
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ViewSet

from approval.models.approval import ApprovalRequest
from approval.models.approval_rule import (
    ApprovalRequestStep,
    ApprovalRequestStepAction,
    ApprovalRule,
    ApprovalRuleLevel,
)
from approval.utils.approval import (
    approve_request,
    can_act,
    cancel_request,
    expire_pending_approvals,
    pending_count_for,
    pending_queryset_for,
    reject_request,
    resolve_rule,
)
from common.core.approval import ApprovalRequired
from common.core.response import ApiResponse
from system.models import UserInfo

pytestmark = pytest.mark.django_db

PENDING = ApprovalRequest.Status.PENDING
APPROVED = ApprovalRequest.Status.APPROVED
REJECTED = ApprovalRequest.Status.REJECTED


class DummyViewSet(ViewSet):
    """审批拦截测试对象（与 test_approval.py 同构：绕开菜单权限矩阵，只测链路）。"""

    permission_classes = [AllowAny]

    @ApprovalRequired()
    def destroy(self, request, pk=None):
        return ApiResponse(data={"executed": True, "pk": pk})


def _enable_interception(paths=None):
    from common.core.config import SysConfig

    SysConfig.set_value("APPROVAL_REQUIRED_PATHS", paths or [r"^/api/test/"])


def _dispatch_delete(user, path="/api/test/1"):
    factory = APIRequestFactory()
    request = factory.delete(path, {}, format="json")
    force_authenticate(request, user=user)
    return DummyViewSet.as_view({"delete": "destroy"})(request, pk="1")


@pytest.fixture
def chain_users(db):
    """三级审批人账号（不含申请人）。"""
    return [UserInfo.objects.create_user(username=f"lvl{index}", password="Test@123456") for index in (1, 2, 3)]


@pytest.fixture
def applicant(db, role):
    return UserInfo.objects.create_user(username="chain_applicant", password="Test@123456")


def _make_rule(usernames, path_patterns=None, priority=0, is_active=True):
    rule = ApprovalRule.objects.create(
        name=f"规则-{priority}",
        path_patterns=path_patterns or [r"^/api/test/"],
        priority=priority,
        is_active=is_active,
    )
    for index, username in enumerate(usernames, start=1):
        ApprovalRuleLevel.objects.create(
            rule=rule,
            name=f"第{index}级",
            order=index,
            assignee_type=ApprovalRuleLevel.AssigneeType.USER,
            assignee_value=username,
        )
    return rule


def _create_chain(applicant, users, path_patterns=None):
    """建一张多级链审批单（走真实拦截链路）。"""
    _enable_interception()
    _make_rule([user.username for user in users], path_patterns=path_patterns)
    response = _dispatch_delete(applicant)
    assert response.status_code == 412, response.data
    return ApprovalRequest.objects.get(creator=applicant)


class TestResolveRule:
    def test_priority_wins(self, applicant, chain_users):
        _make_rule([chain_users[0].username], priority=0)
        high = _make_rule([chain_users[1].username], priority=10)
        assert resolve_rule("/api/test/1").pk == high.pk
        assert resolve_rule("/api/other/1") is None

    def test_inactive_skipped(self, chain_users):
        rule = _make_rule([chain_users[0].username], priority=5, is_active=False)
        assert resolve_rule("/api/test/1") is None
        rule.is_active = True
        rule.save(update_fields=["is_active"])
        assert resolve_rule("/api/test/1").pk == rule.pk


class TestCreateChain:
    def test_builds_snapshot_and_first_level_projection(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        assert record.status == PENDING
        assert record.current_level == 1
        assert list(record.current_assignees.values_list("username", flat=True)) == ["lvl1"]
        steps = list(record.steps.order_by("order"))
        assert [step.order for step in steps] == [1, 2, 3]
        assert [step.status for step in steps] == [PENDING] * 3
        assert list(steps[0].assignees.values_list("username", flat=True)) == ["lvl1"]

    def test_applicant_excluded_from_level(self, applicant, chain_users):
        """级次里同时含申请人与他人：申请人被剔除，他人仍可审。"""
        _make_rule([f"{applicant.username},{chain_users[0].username}"])
        _enable_interception()
        response = _dispatch_delete(applicant)
        assert response.status_code == 412
        record = ApprovalRequest.objects.get(creator=applicant)
        assert list(record.current_assignees.values_list("username", flat=True)) == ["lvl1"]

    @pytest.mark.django_db(transaction=True)
    def test_level_without_available_user_rejects_build(self, applicant):
        """某级只有申请人自己（剔除后为空）→ fail-closed，不产生审批单。

        transaction=True：异常响应会标记请求事务回滚（ATOMIC_REQUESTS），默认的
        测试包裹事务下无法再查询（TransactionManagementError）。
        """
        _enable_interception()
        _make_rule([applicant.username])
        response = _dispatch_delete(applicant)
        assert response.data.get("code") != 1000
        assert ApprovalRequest.objects.filter(creator=applicant).exists() is False


class TestAdvance:
    def test_level_by_level_until_final(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)

        ok, _detail = approve_request(record, chain_users[0], "初审通过")
        assert ok is True
        record.refresh_from_db()
        assert record.status == PENDING
        assert record.current_level == 2
        assert list(record.current_assignees.values_list("username", flat=True)) == ["lvl2"]
        first = record.steps.get(order=1)
        assert first.status == ApprovalRequestStep.Status.APPROVED
        assert first.approver_id == chain_users[0].pk
        assert first.comment == "初审通过"
        assert first.acted_at is not None

        ok, _detail = approve_request(record, chain_users[1])
        assert ok is True
        record.refresh_from_db()
        assert record.status == PENDING
        assert record.current_level == 3

        ok, _detail = approve_request(record, chain_users[2])
        assert ok is True
        record.refresh_from_db()
        assert record.status == APPROVED
        assert record.current_level == 0
        assert record.approver_id == chain_users[2].pk
        assert record.approved_at is not None
        assert record.expired_at is not None
        assert record.current_assignees.count() == 0
        assert record.steps.filter(status=PENDING).count() == 0

    def test_skip_level_rejected(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        ok, detail = approve_request(record, chain_users[1])
        assert ok is False
        # 断言与 gettext 同源（本机有 .mo 显中文、CI 无 .mo 显英文）
        assert str(_("You are not the approver of the current level")) in str(detail)
        record.refresh_from_db()
        assert record.current_level == 1
        assert record.status == PENDING

    def test_outsider_rejected(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        outsider = UserInfo.objects.create_user(username="outsider", password="Test@123456")
        ok, _detail = approve_request(record, outsider)
        assert ok is False
        assert record.steps.get(order=1).status == PENDING


class TestTerminalStates:
    def test_reject_terminates_chain(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        ok, _detail = reject_request(record, chain_users[0], "资料不合规")
        assert ok is True
        record.refresh_from_db()
        assert record.status == REJECTED
        assert record.reason == "资料不合规"
        assert record.current_level == 0
        assert record.current_assignees.count() == 0
        statuses = {step.order: step.status for step in record.steps.all()}
        assert statuses[1] == ApprovalRequestStep.Status.REJECTED
        assert statuses[2] == ApprovalRequestStep.Status.CANCELLED
        assert statuses[3] == ApprovalRequestStep.Status.CANCELLED

    def test_cancel_clears_pending_steps(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        ok, _detail = cancel_request(record, applicant)
        assert ok is True
        record.refresh_from_db()
        assert record.status == ApprovalRequest.Status.CANCELLED
        assert record.current_level == 0
        assert record.steps.filter(status=PENDING).count() == 0

    def test_expire_clears_pending_steps(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        ApprovalRequest.objects.filter(pk=record.pk).update(created_time=timezone.now() - datetime.timedelta(days=10))
        count = expire_pending_approvals(pending_days=3)
        assert count == 1
        record.refresh_from_db()
        assert record.status == ApprovalRequest.Status.EXPIRED
        assert record.current_level == 0
        assert record.steps.filter(status=PENDING).count() == 0
        assert record.current_assignees.count() == 0


class TestPendingScope:
    def test_scope_follows_current_level(self, applicant, chain_users):
        record = _create_chain(applicant, chain_users)
        assert pending_queryset_for(chain_users[0]).filter(pk=record.pk).exists()
        assert not pending_queryset_for(chain_users[1]).filter(pk=record.pk).exists()
        assert pending_count_for(chain_users[0]) == 1
        assert pending_count_for(chain_users[1]) == 0

        ok, _detail = approve_request(record, chain_users[0])
        assert ok is True
        assert not pending_queryset_for(chain_users[0]).filter(pk=record.pk).exists()
        assert pending_queryset_for(chain_users[1]).filter(pk=record.pk).exists()

    def test_mine_excluded_from_pending(self, applicant, chain_users):
        """申请人自己发起的单不进待办（在「我发起」页签处理）。"""
        record = _create_chain(applicant, chain_users)
        assert not pending_queryset_for(applicant).filter(pk=record.pk).exists()
        assert pending_count_for(applicant) == 0


class TestFlatFallback:
    def test_unmatched_path_keeps_flat_flow(self, normal_user, superuser):
        """规则存在但路径不匹配：保持扁平单（全局审批人），无级次。"""
        _enable_interception()
        _make_rule([normal_user.username], path_patterns=[r"^/api/other/"])
        response = _dispatch_delete(normal_user)
        assert response.status_code == 412
        record = ApprovalRequest.objects.get(creator=normal_user)
        assert record.current_level == 0
        assert record.steps.count() == 0
        ok, _detail = approve_request(record, superuser)
        assert ok is True
        record.refresh_from_db()
        assert record.status == APPROVED


def _submit_chain(applicant):
    """在已配置规则的前提下发起拦截（不新建规则）。"""
    _enable_interception()
    response = _dispatch_delete(applicant)
    assert response.status_code == 412, response.data
    return ApprovalRequest.objects.get(creator=applicant)


def _make_multi_rule(first_type, first_users, second_username):
    """两级规则：第一级为多人（OR/AND），第二级单人。"""
    rule = ApprovalRule.objects.create(name=f"多级-{first_type}", path_patterns=[r"^/api/test/"], priority=0)
    ApprovalRuleLevel.objects.create(
        rule=rule,
        name="多人级",
        order=1,
        approve_type=first_type,
        assignee_type=ApprovalRuleLevel.AssigneeType.USER,
        assignee_value=",".join(first_users),
    )
    ApprovalRuleLevel.objects.create(
        rule=rule,
        name="复核级",
        order=2,
        assignee_type=ApprovalRuleLevel.AssigneeType.USER,
        assignee_value=second_username,
    )
    return rule


class TestApproveType:
    """级内多人审批方式：或签（任一人通过）与会签（全部通过）。"""

    def test_or_advances_on_first_approve(self, applicant, chain_users):
        usernames = [chain_users[0].username, chain_users[1].username]
        _make_multi_rule("OR", usernames, chain_users[2].username)
        record = _submit_chain(applicant)
        assert record.steps.get(order=1).approve_type == "OR"

        ok, _detail = approve_request(record, chain_users[0])
        assert ok is True
        record.refresh_from_db()
        # 或签：一人通过即进入下一级
        assert record.current_level == 2
        assert record.steps.get(order=1).status == ApprovalRequestStep.Status.APPROVED

    def test_and_waits_until_all_approve(self, applicant, chain_users):
        usernames = [chain_users[0].username, chain_users[1].username]
        _make_multi_rule("AND", usernames, chain_users[2].username)
        record = _submit_chain(applicant)
        step = record.steps.get(order=1)
        assert step.approve_type == "AND"

        first_ok, detail = approve_request(record, chain_users[0], "我同意")
        assert first_ok is True
        # 会签未齐：返回「已记录，等待其他会签人 (1/2)」，当前级不推进
        assert "1/2" in str(detail)
        record.refresh_from_db()
        assert record.current_level == 1
        step.refresh_from_db()
        assert step.status == ApprovalRequestStep.Status.PENDING
        assert step.actions.filter(approver=chain_users[0]).count() == 1

        # 同一人重复提交：被唯一约束挡住
        again_ok, again_detail = approve_request(record, chain_users[0])
        assert again_ok is False
        assert str(_("You have already handled the current level")) in str(again_detail)

        # 第二人通过 → 该级完成并推进
        second_ok, _detail = approve_request(record, chain_users[1])
        assert second_ok is True
        record.refresh_from_db()
        assert record.current_level == 2
        step.refresh_from_db()
        assert step.status == ApprovalRequestStep.Status.APPROVED
        assert step.actions.count() == 2

    def test_and_reject_terminates_chain(self, applicant, chain_users):
        usernames = [chain_users[0].username, chain_users[1].username]
        _make_multi_rule("AND", usernames, chain_users[2].username)
        record = _submit_chain(applicant)

        ok, _detail = reject_request(record, chain_users[1], "不同意")
        assert ok is True
        record.refresh_from_db()
        assert record.status == REJECTED
        step = record.steps.get(order=1)
        assert step.status == ApprovalRequestStep.Status.REJECTED
        assert step.actions.filter(status=ApprovalRequestStepAction.Status.REJECTED).count() == 1

    def test_can_act_false_after_and_approved(self, applicant, chain_users):
        usernames = [chain_users[0].username, chain_users[1].username]
        _make_multi_rule("AND", usernames, chain_users[2].username)
        record = _submit_chain(applicant)
        approve_request(record, chain_users[0])
        record.refresh_from_db()
        # 会签已通过者不再可操作（前端按钮自然消失），未处理者仍可操作
        assert can_act(record, chain_users[0]) is False
        assert can_act(record, chain_users[1]) is True
