# -*- coding: utf-8 -*-
"""审批委托（审批流三期）集成测试：解析矩阵 + CRUD 与校验。"""

import datetime

import pytest
from django.utils import timezone

from system.models import UserInfo
from system.models.approval import ApprovalDelegation, ApprovalFlow, ApprovalFlowNode
from system.utils.approval_flow import resolve_assignees

pytestmark = pytest.mark.django_db

DELEGATIONS_URL = "/api/system/approval-delegations"


def make_flow(code="deleg_gate"):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    node = ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value="deleg_target",
        condition={},
        timeout_hours=0,
    )
    return flow, node


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="deleg_applicant", password="Test@123456")


@pytest.fixture
def target(db):
    return UserInfo.objects.create_user(username="deleg_target", password="Test@123456")


@pytest.fixture
def agent(db):
    return UserInfo.objects.create_user(username="deleg_agent", password="Test@123456")


def make_delegation(delegator, delegate, **kwargs):
    now = timezone.now()
    return ApprovalDelegation.objects.create(
        delegator=delegator,
        delegate=delegate,
        start_time=kwargs.get("start_time") or now - datetime.timedelta(hours=1),
        end_time=kwargs.get("end_time") or now + datetime.timedelta(hours=1),
        flow_codes=kwargs.get("flow_codes") or [],
        is_active=kwargs.get("is_active", True),
    )


class TestResolveWithDelegation:
    def test_active_delegation_replaces_assignee(self, applicant, target, agent):
        _, node = make_flow()
        make_delegation(target, agent)
        resolved = resolve_assignees(node, applicant, {})
        assert [u.pk for u in resolved] == [agent.pk]

    def test_expired_delegation_falls_back(self, applicant, target, agent):
        _, node = make_flow()
        now = timezone.now()
        make_delegation(
            target,
            agent,
            start_time=now - datetime.timedelta(days=2),
            end_time=now - datetime.timedelta(days=1),
        )
        resolved = resolve_assignees(node, applicant, {})
        assert [u.pk for u in resolved] == [target.pk]

    def test_flow_scope_mismatch_falls_back(self, applicant, target, agent):
        _, node = make_flow("deleg_gate")
        make_delegation(target, agent, flow_codes=["other_flow"])
        resolved = resolve_assignees(node, applicant, {})
        assert [u.pk for u in resolved] == [target.pk]

    def test_inactive_delegation_ignored(self, applicant, target, agent):
        _, node = make_flow()
        make_delegation(target, agent, is_active=False)
        resolved = resolve_assignees(node, applicant, {})
        assert [u.pk for u in resolved] == [target.pk]

    def test_delegate_is_applicant_dropped(self, applicant, target):
        """代理人恰为申请人 → 丢弃该候选（申请人不能审批自己的节点）。"""
        _, node = make_flow()
        make_delegation(target, applicant)
        resolved = resolve_assignees(node, applicant, {})
        assert resolved == []

    def test_no_recursive_delegation(self, applicant, target, agent):
        """代理链不递归：代理人自身再委托不生效。"""
        third = UserInfo.objects.create_user(username="deleg_third", password="Test@123456")
        _, node = make_flow()
        make_delegation(target, agent)
        make_delegation(agent, third)
        resolved = resolve_assignees(node, applicant, {})
        assert [u.pk for u in resolved] == [agent.pk]

    def test_disabled_delegate_dropped(self, applicant, target, agent):
        _, node = make_flow()
        make_delegation(target, agent)
        agent.is_active = False
        agent.save(update_fields=["is_active"])
        resolved = resolve_assignees(node, applicant, {})
        assert resolved == []


class TestDelegationCrud:
    def test_create_and_list(self, auth_client, target, agent):
        now = timezone.now()
        payload = {
            "delegator": str(target.pk),
            "delegate": str(agent.pk),
            "start_time": now.isoformat(),
            "end_time": (now + datetime.timedelta(hours=8)).isoformat(),
            "flow_codes": ["leave"],
            "is_active": True,
            "remark": "出差代审",
        }
        created = auth_client.post(DELEGATIONS_URL, payload, format="json")
        assert created.data["code"] == 1000, created.data
        listed = auth_client.get(DELEGATIONS_URL)
        assert listed.data["data"]["total"] == 1

    def test_same_person_rejected(self, auth_client, target):
        now = timezone.now()
        resp = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(target.pk),
                "start_time": now.isoformat(),
                "end_time": (now + datetime.timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_overlap_rejected(self, auth_client, target, agent):
        now = timezone.now()
        make_delegation(target, agent)
        resp = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(agent.pk),
                "start_time": (now + datetime.timedelta(minutes=10)).isoformat(),
                "end_time": (now + datetime.timedelta(hours=2)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data
