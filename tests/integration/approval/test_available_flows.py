# -*- coding: utf-8 -*-
"""可发起流程按 initiator_roles 收敛：列表可见性与创建入口同一口径。"""

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient

from approval.models.approval import ApprovalFlow, ApprovalFlowNode
from approval.services.approval_flow import create_instance
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

AVAILABLE_URL = "/api/approval/approval-instances/available-flows"


def _flow(code, roles):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], initiator_roles=roles)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value="flow_approver",
        condition={},
        timeout_hours=0,
    )
    return flow


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_user(username="flow_approver", password="Test@123456")


@pytest.fixture
def scoped_client(api_client, normal_user, menu_factory):
    menu = menu_factory(
        "availableFlows:SystemApprovalInstance", "api/approval/approval-instances/available-flows$", "GET"
    )
    normal_user.roles.first().menu.add(menu)
    django_cache.clear()
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=normal_user)
    return client, normal_user


class TestAvailableFlowsScope:
    def test_public_and_matching_role_visible(self, scoped_client, approver):
        client, user = scoped_client
        _flow("public_flow", [])
        _flow("role_flow", ["common"])
        _flow("other_flow", ["finance"])

        resp = client.get(AVAILABLE_URL)
        assert resp.status_code == 200, resp.data
        codes = {row["code"] for row in resp.data["data"]}
        assert codes == {"public_flow", "role_flow"}

    def test_superuser_sees_all(self, auth_client, approver):
        _flow("public_flow", [])
        _flow("other_flow", ["finance"])
        codes = {row["code"] for row in auth_client.get(AVAILABLE_URL).data["data"]}
        assert codes == {"public_flow", "other_flow"}

    def test_direct_create_is_scope_checked(self, scoped_client, approver):
        """绕过列表直调创建同样拒绝：可见性与创建入口共用同一判定。"""
        _client, user = scoped_client
        flow = _flow("other_flow", ["finance"])
        instance, error = create_instance(flow=flow, applicant=user, title="越权发起", form_data={})
        assert instance is None
        assert "not allowed" in error
