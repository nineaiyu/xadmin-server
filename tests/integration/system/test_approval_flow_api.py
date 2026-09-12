# -*- coding: utf-8 -*-
"""全量审批流引擎一期 API 集成：流程定义 CRUD + 流程审批中心主链路 + 菜单权限。"""

import pytest

from system.models import Menu, UserInfo
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask

pytestmark = pytest.mark.django_db

FLOWS_URL = "/api/system/approval-flows"
INSTANCES_URL = "/api/system/approval-instances"


def make_flow(code="leave_api", nodes=None, form_schema=None, is_active=True):
    flow = ApprovalFlow.objects.create(
        name=f"流程-{code}", code=code, form_schema=form_schema or [], is_active=is_active
    )
    for index, node in enumerate(nodes or [{"name": "初审"}]):
        ApprovalFlowNode.objects.create(
            flow=flow,
            name=node.get("name") or f"节点{index + 1}",
            order=node.get("order") or index + 1,
            approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
            assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.USER,
            assignee_value=node.get("assignee_value", "flow_approver"),
            condition=node.get("condition") or {},
            timeout_hours=node.get("timeout_hours") or 0,
        )
    return flow


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_superuser(
        username="flow_approver", email="approver@example.com", password="Test@123456", nickname="审批人"
    )


@pytest.fixture
def approver_role(approver):
    """占位：审批人 fixture 为超管（见 TestApprovalInstanceApi 说明）。"""
    return None


def grant(role, menu_factory, name, path, method):
    """授权菜单权限点：同名复用（menu.name 全局唯一，申请人/审批人 fixture 会授权同一权限码）。"""
    perm = Menu.objects.filter(name=name).first() or menu_factory(name, path=path, method=method)
    role.menu.add(perm)
    return perm


class TestApprovalFlowCrud:
    def test_flow_crud_with_nodes(self, auth_client):
        payload = {
            "name": "请假流程",
            "code": "leave_crud",
            "form_schema": [{"key": "days", "label": "天数", "type": "number", "required": True}],
            "nodes": [
                {"name": "初审", "assignee_type": "user", "assignee_value": "u1"},
                {"name": "终审", "order": 2, "assignee_type": "user", "assignee_value": "u2", "approve_type": "AND"},
            ],
        }
        created = auth_client.post(FLOWS_URL, payload, format="json")
        assert created.data["code"] == 1000
        flow_pk = created.data["data"]["pk"]
        assert created.data["data"]["node_count"] == 2

        listed = auth_client.get(FLOWS_URL, {"code": "leave_crud"})
        assert listed.data["data"]["total"] == 1
        assert listed.data["data"]["results"][0]["nodes"][0]["name"] == "初审"

        # 节点整体替换式更新
        updated = auth_client.patch(
            f"{FLOWS_URL}/{flow_pk}",
            {"nodes": [{"name": "唯一节点", "assignee_type": "user", "assignee_value": "u3"}]},
            format="json",
        )
        assert updated.data["code"] == 1000
        assert ApprovalFlowNode.objects.filter(flow_id=flow_pk).count() == 1

    def test_flow_validation(self, auth_client):
        # 无节点
        no_node = auth_client.post(FLOWS_URL, {"name": "空", "code": "empty_nodes", "nodes": []}, format="json")
        assert no_node.status_code == 400
        # 节点顺序重复
        dup = auth_client.post(
            FLOWS_URL,
            {
                "name": "重复",
                "code": "dup_order",
                "nodes": [
                    {"name": "A", "order": 1, "assignee_type": "user", "assignee_value": "u1"},
                    {"name": "B", "order": 1, "assignee_type": "user", "assignee_value": "u2"},
                ],
            },
            format="json",
        )
        assert dup.status_code == 400
        # 条件运算符白名单
        bad_op = auth_client.post(
            FLOWS_URL,
            {
                "name": "非法条件",
                "code": "bad_condition",
                "nodes": [
                    {
                        "name": "A",
                        "assignee_type": "user",
                        "assignee_value": "u1",
                        "condition": {"field": "amount", "op": "regex", "value": ".*"},
                    }
                ],
            },
            format="json",
        )
        assert bad_op.status_code == 400
        # 角色节点缺 assignee_value
        no_value = auth_client.post(
            FLOWS_URL,
            {
                "name": "缺值",
                "code": "no_value",
                "nodes": [{"name": "A", "assignee_type": "role", "assignee_value": ""}],
            },
            format="json",
        )
        assert no_value.status_code == 400

    def test_flow_delete_guard(self, auth_client, approver):
        flow = make_flow(
            code="delete_guard", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        # 无实例：可删除
        free_flow = make_flow(
            code="free_delete", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        assert auth_client.delete(f"{FLOWS_URL}/{free_flow.pk}").data["code"] == 1000

        # 有实例：拒绝删除（PROTECT 兜底，返回可读错误）
        applicant = UserInfo.objects.create_user(username="delete_guard_user", password="Test@123456")
        ApprovalInstance.objects.create(flow=flow, flow_name=flow.name, title="x", creator=applicant)
        refused = auth_client.delete(f"{FLOWS_URL}/{flow.pk}")
        assert refused.status_code == 400
        assert ApprovalFlow.objects.filter(pk=flow.pk).exists()

    def test_flow_update_blocked_with_pending_instance(self, auth_client, approver):
        flow = make_flow(
            code="pending_lock", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        applicant = UserInfo.objects.create_user(username="pending_lock_user", password="Test@123456")
        ApprovalInstance.objects.create(flow=flow, flow_name=flow.name, title="x", creator=applicant)
        response = auth_client.patch(
            f"{FLOWS_URL}/{flow.pk}",
            {
                "nodes": [
                    {
                        "name": "改节点",
                        "assignee_type": "user",
                        "assignee_value": "flow_approver",
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_flow_list_requires_permission(self, api_client, normal_user, role, menu_factory):
        api_client.force_authenticate(user=normal_user)
        denied = api_client.get(FLOWS_URL)
        assert denied.status_code == 403
        grant(role, menu_factory, "list:SystemApprovalFlow", "api/system/approval-flows$", "GET")
        allowed = api_client.get(FLOWS_URL)
        assert allowed.data["code"] == 1000


class TestApprovalInstanceApi:
    """流程审批中心主链路。

    说明：字段权限为白名单制（无 FieldPermission 配置的角色序列化字段全裁剪），
    这里用超管账号覆盖 API 主链路（既有审批中心集成测试同口径），菜单 RBAC 与
    越权拒绝路径由 test_instance_menu_permission / 越权矩阵用例覆盖。
    """

    @pytest.fixture
    def applicant(self, db):
        return UserInfo.objects.create_superuser(
            username="flow_applicant_su", email="applicant@example.com", password="Test@123456"
        )

    @pytest.fixture
    def approver_client(self, approver, approver_role):
        """独立客户端（不能与申请人共用同一 APIClient：force_authenticate 会互相覆盖）。"""
        from rest_framework.test import APIClient

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        return client

    def test_instance_menu_permission(self, api_client, normal_user, role, menu_factory):
        """普通用户：无菜单权限 403；授权后可读列表（失败关闭）。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(INSTANCES_URL).status_code == 403
        grant(role, menu_factory, "list:SystemApprovalInstance", "api/system/approval-instances$", "GET")
        allowed = api_client.get(INSTANCES_URL)
        assert allowed.data["code"] == 1000
        assert allowed.data["data"]["total"] == 0

    def test_full_lifecycle(self, applicant, approver_client, api_client, approver):
        flow = make_flow(
            code="lifecycle",
            nodes=[
                {"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"},
                {"name": "终审", "assignee_type": "user", "assignee_value": "flow_approver"},
            ],
            form_schema=[{"key": "days", "label": "天数", "type": "number", "required": True}],
        )
        api_client.force_authenticate(user=applicant)
        # 必填校验
        missing = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "请假", "form_data": {}}, format="json"
        )
        assert missing.status_code == 400
        # 正常发起
        created = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "请假申请", "form_data": {"days": 3}}, format="json"
        )
        assert created.data["code"] == 1000
        instance_pk = created.data["data"]["pk"]
        assert created.data["data"]["current_node_name"] == "初审"

        # 我的申请页签可见
        mine = api_client.get(INSTANCES_URL, {"scope": "mine"})
        assert mine.data["data"]["total"] == 1
        # 待办计数（申请人自己不计入）
        count = api_client.get(f"{INSTANCES_URL}/pending-count")
        assert count.data["data"]["pending"] == 0

        # 审批人：待办页签 1 条 + 计数 1
        pending = approver_client.get(INSTANCES_URL, {"scope": "pending"})
        assert pending.data["data"]["total"] == 1
        row = pending.data["data"]["results"][0]
        assert row["my_task"]["pk"]
        assert approver_client.get(f"{INSTANCES_URL}/pending-count").data["data"]["pending"] == 1

        # 通过首节点 → 流转到终审
        approved = approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"comment": "同意"}, format="json")
        assert approved.data["code"] == 1000
        instance = ApprovalInstance.objects.get(pk=instance_pk)
        assert instance.current_node.name == "终审"
        # 已办页签可见
        assert approver_client.get(INSTANCES_URL, {"scope": "done"}).data["data"]["total"] == 1

        # 终审通过 → 结束
        final = approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {}, format="json")
        assert final.data["code"] == 1000
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.APPROVED
        # 详情：任务轨迹完整（首节点 1 条通过 + 终审 1 条通过）
        detail = approver_client.get(f"{INSTANCES_URL}/{instance_pk}")
        assert detail.data["code"] == 1000
        assert len(detail.data["data"]["tasks"]) == 2
        # 统计口径
        stats = approver_client.get(f"{INSTANCES_URL}/stats")
        assert stats.data["data"]["approved"] == 2

    def test_reject_requires_reason_and_cancel_flow(self, applicant, approver_client, api_client, approver):
        flow = make_flow(
            code="reject_api", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        api_client.force_authenticate(user=applicant)
        instance_pk = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "报销", "form_data": {}}, format="json"
        ).data["data"]["pk"]

        no_reason = approver_client.post(f"{INSTANCES_URL}/{instance_pk}/reject", {}, format="json")
        assert no_reason.status_code == 400
        rejected = approver_client.post(f"{INSTANCES_URL}/{instance_pk}/reject", {"reason": "超预算"}, format="json")
        assert rejected.data["code"] == 1000
        instance = ApprovalInstance.objects.get(pk=instance_pk)
        assert instance.status == ApprovalInstance.Status.REJECTED
        assert instance.reason == "超预算"

        # 已结束的申请不能再撤回
        cancelled = api_client.post(f"{INSTANCES_URL}/{instance_pk}/cancel", {}, format="json")
        assert cancelled.data["code"] == 1001

    def test_cancel_and_add_sign(self, applicant, approver_client, api_client, auth_client, approver):
        flow = make_flow(
            code="add_sign_api",
            nodes=[
                {
                    "name": "会签",
                    "approve_type": ApprovalFlowNode.ApproveType.AND,
                    "assignee_type": "user",
                    "assignee_value": "flow_approver",
                }
            ],
        )
        api_client.force_authenticate(user=applicant)
        instance_pk = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "加签", "form_data": {}}, format="json"
        ).data["data"]["pk"]

        # 加签新审批人（超管可代为加签）
        extra = UserInfo.objects.create_superuser(
            username="flow_extra", email="extra@example.com", password="Test@123456"
        )
        auth_client.post(f"{INSTANCES_URL}/{instance_pk}/add-sign", {"usernames": "flow_extra"}, format="json")
        assert ApprovalNodeTask.objects.filter(instance_id=instance_pk, assignee=extra, is_added=True).exists()

        # 会签：两个审批人都通过后才结束
        for user in (approver, extra):
            approver_client.force_authenticate(user=user)
            task = ApprovalNodeTask.objects.get(instance_id=instance_pk, assignee=user, status="PENDING")
            assert approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"task": str(task.pk)}, format="json")
        assert ApprovalInstance.objects.get(pk=instance_pk).status == ApprovalInstance.Status.APPROVED

        # 撤回：新建一单由申请人撤回
        instance_pk2 = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "撤回", "form_data": {}}, format="json"
        ).data["data"]["pk"]
        assert api_client.post(f"{INSTANCES_URL}/{instance_pk2}/cancel", {}, format="json").data["code"] == 1000
        assert ApprovalInstance.objects.get(pk=instance_pk2).status == ApprovalInstance.Status.CANCELLED

    def test_scope_isolation(self, applicant, approver_client, api_client, approver, role, menu_factory):
        """他人不可见：非参与用户（有列表权限）看不到实例（可见域收口）。"""
        flow = make_flow(
            code="isolation", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        api_client.force_authenticate(user=applicant)
        api_client.post(INSTANCES_URL, {"flow": str(flow.pk), "title": "隔离", "form_data": {}}, format="json")

        outsider = UserInfo.objects.create_user(username="flow_outsider", password="Test@123456")
        outsider.roles.add(role)
        grant(role, menu_factory, "list:SystemApprovalInstance", "api/system/approval-instances$", "GET")
        api_client.force_authenticate(user=outsider)
        assert api_client.get(INSTANCES_URL).data["data"]["total"] == 0

    def test_batch_approve_and_reject(self, applicant, approver_client, api_client):
        flow = make_flow(
            code="batch_api", nodes=[{"name": "初审", "assignee_type": "user", "assignee_value": "flow_approver"}]
        )
        api_client.force_authenticate(user=applicant)
        first = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "批一", "form_data": {}}, format="json"
        ).data["data"]["pk"]
        second = api_client.post(
            INSTANCES_URL, {"flow": str(flow.pk), "title": "批二", "form_data": {}}, format="json"
        ).data["data"]["pk"]

        approved = approver_client.post(
            f"{INSTANCES_URL}/batch-approve", {"pks": [first], "comment": "批量同意"}, format="json"
        )
        assert approved.data["data"]["succeeded"] == 1
        rejected = approver_client.post(
            f"{INSTANCES_URL}/batch-reject", {"pks": [second], "reason": "批量驳回"}, format="json"
        )
        assert rejected.data["data"]["succeeded"] == 1
        assert ApprovalInstance.objects.get(pk=first).status == ApprovalInstance.Status.APPROVED
        assert ApprovalInstance.objects.get(pk=second).status == ApprovalInstance.Status.REJECTED

    # 二期（ADR-016）：条件分支主链路 API + 版本列表/回滚 API

    def test_phase2_branch_api_lifecycle(self, api_client, applicant, approver_client, approver, menu_factory, role):
        """金额条件分支：小额走快车道直达归档节点，全程未经过大额终审。"""
        grant(role, menu_factory, "list:SystemApprovalFlow", "api/system/approval-flows$", "GET")
        grant(role, menu_factory, "add:SystemApprovalFlow", "api/system/approval-flows$", "POST")
        grant(role, menu_factory, "list:SystemApprovalInstance", "api/system/approval-instances$", "GET")
        grant(role, menu_factory, "add:SystemApprovalInstance", "api/system/approval-instances$", "POST")
        grant(role, menu_factory, "approve:SystemApprovalInstance", "api/system/approval-instances/approve$", "POST")
        grant(role, menu_factory, "change:SystemApprovalFlow", "api/system/approval-flows$", "PUT")
        api_client.force_authenticate(user=applicant)

        resp = api_client.post(
            FLOWS_URL,
            {
                "name": "分支流程",
                "code": "phase2_branch",
                "form_schema": [{"key": "amount", "label": "金额", "type": "number", "required": True}],
                "nodes": [
                    {"name": "初审", "order": 1, "assignee_type": "user", "assignee_value": "flow_approver"},
                    {
                        "name": "大额终审",
                        "order": 2,
                        "assignee_type": "user",
                        "assignee_value": "flow_approver",
                        "condition": {"field": "amount", "op": "gte", "value": 1000},
                    },
                    {
                        "name": "小额出口",
                        "order": 3,
                        "assignee_type": "user",
                        "assignee_value": "flow_approver",
                        "routes": [{"condition": {"field": "amount", "op": "lt", "value": 1000}, "target": 4}],
                    },
                    {"name": "归档", "order": 4, "assignee_type": "user", "assignee_value": "flow_approver"},
                ],
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        flow_pk = resp.data["data"]["pk"]

        resp = api_client.post(
            INSTANCES_URL, {"flow": flow_pk, "title": "小额单", "form_data": {"amount": 100}}, format="json"
        )
        assert resp.status_code == 200, resp.data
        instance_pk = resp.data["data"]["pk"]
        # 初审通过 → 进入节点 3
        task1 = ApprovalNodeTask.objects.get(instance_id=instance_pk, node_order=1, assignee=approver)
        assert (
            approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"task": str(task1.pk)}, format="json").data[
                "code"
            ]
            == 1000
        )
        assert ApprovalInstance.objects.get(pk=instance_pk).current_node.order == 3
        # 节点 3 通过 → 路由直达节点 4 → 归档通过后结束
        task3 = ApprovalNodeTask.objects.get(instance_id=instance_pk, node_order=3, assignee=approver)
        approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"task": str(task3.pk)}, format="json")
        task4 = ApprovalNodeTask.objects.get(instance_id=instance_pk, node_order=4, assignee=approver)
        approver_client.post(f"{INSTANCES_URL}/{instance_pk}/approve", {"task": str(task4.pk)}, format="json")
        assert ApprovalInstance.objects.get(pk=instance_pk).status == ApprovalInstance.Status.APPROVED
        assert ApprovalNodeTask.objects.filter(instance_id=instance_pk, node_order=2).exists() is False

    def test_phase2_versions_and_rollback_api(self, api_client, applicant, approver, menu_factory, role):
        """版本列表 + 回滚 API：有 PENDING 实例时回滚被拒，无在途时成功。"""
        grant(role, menu_factory, "list:SystemApprovalFlow", "api/system/approval-flows$", "GET")
        grant(role, menu_factory, "add:SystemApprovalFlow", "api/system/approval-flows$", "POST")
        grant(role, menu_factory, "change:SystemApprovalFlow", "api/system/approval-flows$", "PUT")
        grant(role, menu_factory, "list:SystemApprovalInstance", "api/system/approval-instances$", "GET")
        grant(role, menu_factory, "add:SystemApprovalInstance", "api/system/approval-instances$", "POST")
        api_client.force_authenticate(user=applicant)

        resp = api_client.post(
            FLOWS_URL,
            {
                "name": "版本流程",
                "code": "phase2_ver",
                "nodes": [{"name": "节点一", "order": 1, "assignee_type": "user", "assignee_value": "flow_approver"}],
            },
            format="json",
        )
        flow_pk = resp.data["data"]["pk"]

        # 变更定义 → v2
        api_client.put(
            f"{FLOWS_URL}/{flow_pk}",
            {
                "name": "版本流程",
                "code": "phase2_ver",
                "nodes": [
                    {"name": "节点一", "order": 1, "assignee_type": "user", "assignee_value": "flow_approver"},
                    {"name": "节点二", "order": 2, "assignee_type": "user", "assignee_value": "flow_approver"},
                ],
            },
            format="json",
        )
        versions = api_client.get(f"{FLOWS_URL}/{flow_pk}/versions").data["data"]
        assert [item["version"] for item in versions] == [2, 1]

        # 有 PENDING 实例：回滚 400
        created = api_client.post(INSTANCES_URL, {"flow": flow_pk, "title": "在途单", "form_data": {}}, format="json")
        assert created.data["code"] == 1000, created.data
        instance_pk = created.data["data"]["pk"]
        blocked = api_client.post(f"{FLOWS_URL}/{flow_pk}/rollback", {"version": 1}, format="json")
        assert blocked.data["code"] != 1000, blocked.data

        # 结束在途（撤回）→ 回滚成功：节点恢复为 1 个 + 落 v3
        api_client.post(f"{INSTANCES_URL}/{instance_pk}/cancel", {}, format="json")
        rolled = api_client.post(f"{FLOWS_URL}/{flow_pk}/rollback", {"version": 1, "remark": "恢复初始"}, format="json")
        assert rolled.data["code"] == 1000, rolled.data
        detail = api_client.get(f"{FLOWS_URL}/{flow_pk}").data["data"]
        assert len(detail["nodes"]) == 1
        versions = api_client.get(f"{FLOWS_URL}/{flow_pk}/versions").data["data"]
        assert versions[0]["version"] == 3
