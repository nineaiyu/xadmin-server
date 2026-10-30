# -*- coding: utf-8 -*-
"""审批规则/委托影响面预览：只读、在途口径、优先级对比。"""

import datetime

import pytest
from django.utils import timezone

from approval.models.approval import ApprovalDelegation, ApprovalFlowNode
from approval.models.approval_flow_def import ApprovalFlow
from approval.models.approval_instance import ApprovalInstance, ApprovalNodeTask
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
        """影响面查委托展开的同一实体（ApprovalNodeTask）：委托人名下在途待办计数。"""
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
        # 在途流程实例 + 挂在委托人名下的待办任务（引擎展开委托的实体即 NodeTask）
        flow = ApprovalFlow.objects.create(name="委托影响流程", code="impact_del", is_active=True)
        node = ApprovalFlowNode.objects.create(
            flow=flow,
            name="初审",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=target.username,
        )
        record = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title="委托影响单", creator=superuser, current_node=node
        )
        ApprovalNodeTask.objects.create(instance=record, node=node, node_name="初审", node_order=1, assignee=target)
        resp = auth_client.get(f"{DELEGATION_URL}/{delegation.pk}/impact")
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["total"] >= 1
        assert data["delegator_pending"] >= 1
        assert data["items"][0]["pk"] == str(record.pk)

    def _delegation(self, delegator, delegate):
        now = timezone.now()
        return ApprovalDelegation.objects.create(
            delegator=delegator,
            delegate=delegate,
            start_time=now,
            end_time=now + datetime.timedelta(hours=1),
            is_active=True,
        )

    def _instance_with_task(self, creator, node_name, assignee, delegate_from=None):
        flow = ApprovalFlow.objects.create(name=f"委托流程{node_name}", code=f"impact_{node_name}", is_active=True)
        node = ApprovalFlowNode.objects.create(
            flow=flow,
            name=node_name,
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=assignee.username,
        )
        instance = ApprovalInstance.objects.create(
            flow=flow, flow_name=flow.name, title=f"委托单{node_name}", creator=creator, current_node=node
        )
        ApprovalNodeTask.objects.create(
            instance=instance,
            node=node,
            node_name=node_name,
            node_order=1,
            assignee=assignee,
            delegate_from=delegate_from,
        )
        return instance

    def test_delegate_tasks_counted_only_when_from_this_delegator(self, auth_client, superuser):
        """代理人名下的待办：只有 delegate_from=本委托人 的代审任务计入影响面。

        同一个人可能是多条委托的代理人，凭「assignee=代理人」直接统计会把
        别的委托（乃至无委托的自有任务）算进本委托的停用影响，导致停用前
        的确认面虚高——引擎展开时只认 delegate_from 匹配的那部分。
        """
        target = UserInfo.objects.create_user(username="impact_target2", password="Test@123456")
        other = UserInfo.objects.create_user(username="impact_other2", password="Test@123456")
        agent = UserInfo.objects.create_user(username="impact_agent2", password="Test@123456")
        delegation = self._delegation(target, agent)
        own = self._instance_with_task(superuser, "代审", agent, delegate_from=target)
        other_task = self._instance_with_task(superuser, "他委托", agent, delegate_from=other)
        self._instance_with_task(superuser, "自有", agent)

        resp = auth_client.get(f"{DELEGATION_URL}/{delegation.pk}/impact")
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["delegate_pending"] == 1
        assert data["total"] == 1
        listed = {item["pk"] for item in data["items"]}
        assert listed == {str(own.pk)}
        assert str(other_task.pk) not in listed
