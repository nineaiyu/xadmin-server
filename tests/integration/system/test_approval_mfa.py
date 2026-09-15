# -*- coding: utf-8 -*-
"""审批动作 MFA 二次确认（审批流三期）集成测试。

覆盖：APPROVAL_MFA_REQUIRED_ACTIONS 关闭时零影响 / 命中动作未验证返回 412
（user_confirm_required）/ 验证通过后放行 / 未命中动作不受影响。
"""

import pytest

from common.core.config import SysConfig
from mfa.cache import UserConfirmStateCache
from mfa.const import ConfirmType
from system.models import UserInfo
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask

pytestmark = pytest.mark.django_db

INSTANCES_URL = "/api/system/approval-instances"


def make_flow(code="mfa_gate", approver_name="flow_approver_mfa"):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver_name,
        condition={},
        timeout_hours=0,
    )
    return flow


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_superuser(
        username="mfa_applicant", email="applicant@example.com", password="Test@123456", nickname="申请人"
    )


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_superuser(
        username="flow_approver_mfa", email="approver@example.com", password="Test@123456", nickname="审批人"
    )


@pytest.fixture
def approver_client(api_client, approver):
    api_client.force_authenticate(user=approver)
    return api_client


@pytest.fixture
def pending_instance(applicant, approver):
    """待办实例（跳过发起链路，直接构造实例 + 待办任务，聚焦动作层门控）。"""
    flow = make_flow()
    node = flow.nodes.first()
    instance = ApprovalInstance.objects.create(
        flow=flow,
        flow_name=flow.name,
        title="MFA 门控测试",
        form_data={},
        status=ApprovalInstance.Status.PENDING,
        current_node=node,
        creator=applicant,
    )
    ApprovalNodeTask.objects.create(
        instance=instance,
        node=node,
        node_name=node.name,
        node_order=node.order,
        assignee=approver,
        status=ApprovalNodeTask.Status.PENDING,
    )
    return instance


def set_mfa_actions(monkeypatch, actions):
    """覆盖 SysConfig 配置（property 只读，monkeypatch 类属性）。"""
    monkeypatch.setattr(
        type(SysConfig),
        "APPROVAL_MFA_REQUIRED_ACTIONS",
        property(lambda self: actions),
        raising=False,
    )


class TestApprovalMfaGate:
    def test_default_disabled_passes(self, approver_client, pending_instance):
        """默认空清单 = 不启用：审批动作直通。"""
        resp = approver_client.post(f"{INSTANCES_URL}/{pending_instance.pk}/approve", {}, format="json")
        assert resp.data["code"] == 1000, resp.data

    def test_required_action_without_confirm_returns_412(self, monkeypatch, approver_client, pending_instance):
        """命中 approve：未二次验证 → 412 user_confirm_required，且业务状态不推进。"""
        set_mfa_actions(monkeypatch, ["approve"])
        resp = approver_client.post(f"{INSTANCES_URL}/{pending_instance.pk}/approve", {}, format="json")
        assert resp.status_code == 412, resp.data
        assert resp.data["type"] == "user_confirm_required"
        assert resp.data["confirm_type"] == "mfa"
        pending_instance.refresh_from_db()
        assert pending_instance.status == ApprovalInstance.Status.PENDING

    def test_required_action_after_confirm_passes(self, monkeypatch, approver_client, approver, pending_instance):
        """验证通过（确认状态缓存有效）→ 放行。"""
        set_mfa_actions(monkeypatch, ["approve"])
        UserConfirmStateCache(approver).set(ConfirmType.MFA, "otp")
        resp = approver_client.post(f"{INSTANCES_URL}/{pending_instance.pk}/approve", {}, format="json")
        assert resp.data["code"] == 1000, resp.data

    def test_other_action_unaffected(self, monkeypatch, approver_client, pending_instance):
        """仅 approve 命中时，reject 不受影响（逐动作粒度）。"""
        set_mfa_actions(monkeypatch, ["approve"])
        resp = approver_client.post(
            f"{INSTANCES_URL}/{pending_instance.pk}/reject", {"reason": "不合规"}, format="json"
        )
        assert resp.data["code"] == 1000, resp.data
