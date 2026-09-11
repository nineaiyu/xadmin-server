# -*- coding: utf-8 -*-
"""越权矩阵扩展（F2 全量审批流引擎一期）：覆盖流程定义 / 流程实例的新攻击面（M30-M37）。

与 `test_privilege_escalation_matrix.py` / `test_privilege_matrix_phase5.py` 同口径
（HTTP 集成、菜单授权按生产正则惯例），编号顺延。

| 编号 | 层 | 攻击面 | 预期防线 |
|------|----|--------|----------|
| M30 | 认证边界 | 匿名访问流程定义 / 流程实例 | 401 |
| M31 | 垂直越权 | 无流程定义权限创建流程 | 403 |
| M32 | 垂直越权 | 无发起权限创建申请 | 403 |
| M33 | 水平越权 | 有审批权限但非节点指派人处理任务 | 400 且实例状态不变 |
| M34 | 水平越权 | 非参与用户查看他人实例详情 | 404（可见域收口） |
| M35 | 水平越权 | 非节点参与人加签他人实例 | 403 |
| M36 | 水平越权 | 非申请人撤回他人申请 | 业务失败且状态不变 |
| M37 | 垂直越权 | 普通用户改动流程节点（越权配置工作流） | 403 |
"""

import pytest

from system.models import UserInfo
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask

pytestmark = pytest.mark.django_db

FLOWS_URL = "/api/system/approval-flows"
INSTANCES_URL = "/api/system/approval-instances"


def grant(role, menu_factory, path, method, name):
    menu = menu_factory(name, path=path, method=method)
    role.menu.add(menu)
    return menu


@pytest.fixture
def alice(normal_user):
    """攻击方：普通用户。"""
    return normal_user


@pytest.fixture
def bob():
    return UserInfo.objects.create_user(username="bob-flow-matrix", password="Bob-Pwd-2026!")


@pytest.fixture
def flow(bob):
    """bob 是流程审批人（节点指派）——alice 不在任何节点。"""
    instance_flow = ApprovalFlow.objects.create(name="矩阵流程", code="matrix_flow")
    ApprovalFlowNode.objects.create(
        flow=instance_flow,
        name="初审",
        order=1,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=bob.username,
    )
    return instance_flow


@pytest.fixture
def carol():
    """跨节点参与人：持有第二节点任务（可见实例，但不是当前节点待办人）。"""
    return UserInfo.objects.create_user(username="carol-flow-matrix", password="Carol-Pwd-2026!")


@pytest.fixture
def instance(flow, bob, carol):
    record = ApprovalInstance.objects.create(
        flow=flow, flow_name=flow.name, title="矩阵申请", creator=bob, current_node=flow.nodes.first()
    )
    ApprovalNodeTask.objects.create(
        instance=record, node=flow.nodes.first(), node_name="初审", node_order=1, assignee=bob
    )
    node2 = ApprovalFlowNode.objects.create(
        flow=flow,
        name="终审",
        order=2,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=carol.username,
    )
    ApprovalNodeTask.objects.create(instance=record, node=node2, node_name="终审", node_order=2, assignee=carol)
    return record


class TestApprovalFlowPrivilegeMatrix:
    def test_m30_anonymous_access_denied(self, api_client, instance):
        """M30：匿名访问流程定义 / 流程实例入口均 401。"""
        assert api_client.get(FLOWS_URL).status_code == 401
        assert api_client.get(INSTANCES_URL).status_code == 401

    def test_m31_create_flow_requires_permission(self, api_client, alice, flow):
        """M31：无 create 权限创建流程被拒（垂直越权）。"""
        api_client.force_authenticate(user=alice)
        resp = api_client.post(
            FLOWS_URL,
            {
                "name": "越权流程",
                "code": "escalation_flow",
                "nodes": [{"name": "A", "assignee_type": "user", "assignee_value": "bob-flow-matrix"}],
            },
            format="json",
        )
        assert resp.status_code == 403
        assert not ApprovalFlow.objects.filter(code="escalation_flow").exists()

    def test_m32_create_instance_requires_permission(self, api_client, alice, flow):
        """M32：无 create 权限发起申请被拒（垂直越权）。"""
        api_client.force_authenticate(user=alice)
        resp = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "越权申请", "form_data": {}}, format="json"
        )
        assert resp.status_code == 403
        assert ApprovalInstance.objects.count() == 0

    def test_m33_approve_requires_current_node_assignee(self, api_client, carol, role, menu_factory, instance):
        """M33：跨节点参与人（可见实例但非当前节点待办人）审批 → 400，状态不变。"""
        grant(role, menu_factory, "api/system/approval-instances/(?P<pk>[^/.]+)/approve$", "POST", "flow-approve")
        carol.roles.add(role)
        api_client.force_authenticate(user=carol)
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/approve", {}, format="json")
        assert resp.status_code == 400
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        assert ApprovalNodeTask.objects.filter(instance=instance, status=ApprovalNodeTask.Status.PENDING).count() == 2

    def test_m34_retrieve_others_instance_denied(self, api_client, alice, role, menu_factory, instance):
        """M34：非参与用户查看他人实例详情 → 数据权限拒绝（400/404 口径）。"""
        grant(
            role,
            menu_factory,
            "api/system/approval-instances/(?P<pk>[^/.]+)$",
            "GET",
            "flow-instance-detail",
        )
        api_client.force_authenticate(user=alice)
        resp = api_client.get(f"{INSTANCES_URL}/{instance.pk}")
        assert resp.status_code in (400, 404)

    def test_m35_add_sign_requires_current_node_participant(self, api_client, carol, role, menu_factory, instance):
        """M35：非当前节点参与人加签 → 业务失败，任务集不变。"""
        grant(role, menu_factory, "api/system/approval-instances/(?P<pk>[^/.]+)/add-sign$", "POST", "flow-add-sign")
        carol.roles.add(role)
        api_client.force_authenticate(user=carol)
        before = ApprovalNodeTask.objects.filter(instance=instance).count()
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/add-sign", {"usernames": carol.username}, format="json")
        assert resp.data["code"] == 1001
        assert ApprovalNodeTask.objects.filter(instance=instance).count() == before

    def test_m36_cancel_requires_applicant(self, api_client, carol, role, menu_factory, instance):
        """M36：非申请人撤回他人申请 → 业务失败，实例仍为 PENDING。"""
        grant(role, menu_factory, "api/system/approval-instances/(?P<pk>[^/.]+)/cancel$", "POST", "flow-cancel")
        carol.roles.add(role)
        api_client.force_authenticate(user=carol)
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/cancel", {}, format="json")
        assert resp.data["code"] == 1001
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING

    def test_m37_update_flow_requires_permission(self, api_client, alice, flow):
        """M37：普通用户改动流程节点（越权配置工作流）被拒。"""
        api_client.force_authenticate(user=alice)
        resp = api_client.patch(
            f"{FLOWS_URL}/{flow.pk}",
            {"nodes": [{"name": "篡改", "assignee_type": "user", "assignee_value": alice.username}]},
            format="json",
        )
        assert resp.status_code == 403
        assert ApprovalFlowNode.objects.filter(flow=flow, name="篡改").count() == 0
