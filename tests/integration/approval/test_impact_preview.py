# -*- coding: utf-8 -*-
"""审批规则/委托影响面预览：只读、在途口径、优先级对比。"""

import datetime

import pytest
from django.utils import timezone

from approval.models.approval import ApprovalDelegation
from approval.models.approval_request import ApprovalRequest
from approval.models.approval_rule import ApprovalRule, ApprovalRuleLevel
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

RULE_URL = "/api/approval/approval-rules"
DELEGATION_URL = "/api/approval/approval-delegations"


def _pending_request(creator, path="/api/demo/book/1", method="DELETE"):
    return ApprovalRequest.objects.create(
        method=method,
        path=path,
        module="demo",
        creator=creator,
        status=ApprovalRequest.Status.PENDING,
    )


def _rule(name, priority, pattern=r"^/api/demo/book/"):
    rule = ApprovalRule.objects.create(name=name, path_patterns=[pattern], priority=priority, is_active=True)
    ApprovalRuleLevel.objects.create(
        rule=rule,
        order=1,
        name="初审",
        assignee_type=ApprovalRuleLevel.AssigneeType.USER,
        assignee_value="approver",
    )
    return rule


class TestRuleImpactPreview:
    def test_preview_lists_pending_request_and_priority_result(self, auth_client, superuser):
        _pending_request(superuser)
        rule = _rule("草稿规则", 5)
        resp = auth_client.post(
            f"{RULE_URL}/impact-preview",
            {"pk": str(rule.pk)},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["total"] == 1
        assert data["items"][0]["path"] == "/api/demo/book/1"
        assert data["items"][0]["draft_wins"] is True

    def test_preview_reports_higher_priority_rule(self, auth_client, superuser):
        _pending_request(superuser)
        _rule("高优规则", 100)
        rule = _rule("低优规则", 5)
        resp = auth_client.post(f"{RULE_URL}/impact-preview", {"pk": str(rule.pk)}, format="json")
        data = resp.data["data"]
        assert data["items"][0]["current_rule"] == "高优规则"
        assert data["items"][0]["draft_wins"] is False

    def test_inactive_draft_has_no_hits(self, auth_client, superuser):
        _pending_request(superuser)
        rule = _rule("停用规则", 5)
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        resp = auth_client.post(f"{RULE_URL}/impact-preview", {"pk": str(rule.pk)}, format="json")
        assert resp.data["data"]["total"] == 0


class TestDelegationImpact:
    def test_disable_impact_counts_pending_assignments(self, auth_client, superuser):
        target = UserInfo.objects.create_user(username="impact_target", password="Test@123456")
        agent = UserInfo.objects.create_user(username="impact_agent", password="Test@123456")
        now = timezone.now()
        delegation = ApprovalDelegation.objects.create(
            delegator=target,
            delegate=agent,
            start_time=now,
            end_time=now + datetime.timedelta(hours=1),
            is_active=True,
        )
        pending = _pending_request(superuser)
        pending.current_assignees.add(target)
        resp = auth_client.get(f"{DELEGATION_URL}/{delegation.pk}/impact")
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["total"] >= 1
        assert data["delegator_pending"] >= 1
