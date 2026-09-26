# -*- coding: utf-8 -*-
"""审批协作增强集成测试：讨论区评论 + 抄送人 + 可见域。

口径钉死：
- 评论内容落库 + @ 提及解析为用户名（排除自己），提及者收提醒；
- 删除仅作者本人或超管；
- 实例抄送人 = 可达节点 cc_users 并集 + 发起时追加（去重、不含申请人）；
- 抄送人进入实例可见域（可查看详情、参与讨论）。
"""

import pytest

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance
from approval.utils.approval_flow import create_instance, visible_instances_for
from system.models import UserInfo

pytestmark = pytest.mark.django_db

INSTANCES_URL = "/api/system/approval-instances"


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="collab_applier", password="Test@123456", nickname="申请人")


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_user(username="collab_approver", password="Test@123456", nickname="审批人")


@pytest.fixture
def cc_user(db):
    return UserInfo.objects.create_user(username="collab_cc", password="Test@123456", nickname="抄送人")


def make_flow(approver, cc_users=None):
    flow = ApprovalFlow.objects.create(name="协作流程", code="collab_flow")
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver.username,
        cc_users=[str(user.pk) for user in (cc_users or [])],
    )
    return flow


class TestInstanceCc:
    def test_node_cc_becomes_instance_cc(self, applicant, approver, cc_user):
        flow = make_flow(approver, cc_users=[cc_user])
        instance, error = create_instance(flow=flow, applicant=applicant, title="抄送测试", form_data={})
        assert error is None
        assert list(instance.cc_users.values_list("pk", flat=True)) == [cc_user.pk]

    def test_extra_cc_users_merged_and_deduped(self, applicant, approver, cc_user):
        extra = UserInfo.objects.create_user(username="collab_cc2", password="Test@123456", nickname="追加抄送")
        flow = make_flow(approver, cc_users=[cc_user])
        instance, error = create_instance(
            flow=flow,
            applicant=applicant,
            title="追加抄送",
            form_data={},
            cc_users=[str(cc_user.pk), str(extra.pk), "not-a-pk"],
        )
        assert error is None
        cc_pks = set(instance.cc_users.values_list("pk", flat=True))
        assert cc_pks == {cc_user.pk, extra.pk}

    def test_applicant_not_in_cc(self, applicant, approver):
        flow = make_flow(approver)
        instance, error = create_instance(
            flow=flow, applicant=applicant, title="申请人自抄送", form_data={}, cc_users=[str(applicant.pk)]
        )
        assert error is None
        assert instance.cc_users.count() == 0

    def test_cc_accepts_username_identifier(self, applicant, approver, cc_user):
        """发起时追加抄送：标识兼容用户名（前端设计器/发起弹窗口径）"""
        flow = make_flow(approver)
        instance, error = create_instance(
            flow=flow, applicant=applicant, title="用户名抄送", form_data={}, cc_users=[cc_user.username]
        )
        assert error is None
        assert list(instance.cc_users.values_list("pk", flat=True)) == [cc_user.pk]

    def test_node_cc_accepts_username(self, applicant, approver, cc_user):
        """节点级默认抄送：标识兼容用户名（流程设计器保存的形态）"""
        flow = ApprovalFlow.objects.create(name="用户名抄送流程", code="collab_flow_uname")
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="初审",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=approver.username,
            cc_users=[cc_user.username],
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="节点用户名抄送", form_data={})
        assert error is None
        assert list(instance.cc_users.values_list("pk", flat=True)) == [cc_user.pk]

    def test_cc_user_in_visible_scope(self, applicant, approver, cc_user):
        flow = make_flow(approver, cc_users=[cc_user])
        instance, _error = create_instance(flow=flow, applicant=applicant, title="可见域", form_data={})
        assert instance.pk
        assert visible_instances_for(cc_user).filter(pk=instance.pk).exists()
        outsider = UserInfo.objects.create_user(username="collab_outsider", password="Test@123456")
        assert not visible_instances_for(outsider).filter(pk=instance.pk).exists()

    def test_create_api_accepts_cc_users(self, auth_client, applicant, approver, cc_user):
        flow = make_flow(approver)
        resp = auth_client.post(
            INSTANCES_URL,
            {"flow": str(flow.pk), "title": "API 抄送", "form_data": {}, "cc_users": [str(cc_user.pk)]},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        instance = ApprovalInstance.objects.get(pk=resp.data["data"]["pk"])
        assert list(instance.cc_users.values_list("pk", flat=True)) == [cc_user.pk]
        assert resp.data["data"]["cc_users"][0]["label"] == cc_user.nickname


class TestDiscussion:
    def _make_instance(self, applicant, approver):
        flow = make_flow(approver)
        instance, error = create_instance(flow=flow, applicant=applicant, title="讨论", form_data={})
        assert error is None
        return instance

    def test_comment_with_mention(self, auth_client, applicant, approver, cc_user):
        instance = self._make_instance(applicant, approver)
        resp = auth_client.post(
            f"{INSTANCES_URL}/{instance.pk}/comment",
            {"content": f"@{approver.username} @{cc_user.username} 请补充说明"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["author_display"]
        assert set(str(pk) for pk in data["mentions"]) == {str(approver.pk), str(cc_user.pk)}

        resp = auth_client.get(f"{INSTANCES_URL}/{instance.pk}/comments")
        assert resp.data["code"] == 1000, resp.data
        assert len(resp.data["data"]) == 1

    def test_comment_validation(self, auth_client, applicant, approver):
        instance = self._make_instance(applicant, approver)
        resp = auth_client.post(f"{INSTANCES_URL}/{instance.pk}/comment", {"content": "  "}, format="json")
        assert resp.data["code"] == 1004
        resp = auth_client.post(f"{INSTANCES_URL}/{instance.pk}/comment", {"content": "x" * 2001}, format="json")
        assert resp.data["code"] == 1004

    def test_delete_comment_permission(self, api_client, superuser, applicant, approver, role, menu_factory):
        """作者可删；其他普通用户被拒（403）；超管可删"""
        from system.models import Menu

        instance = self._make_instance(applicant, approver)
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/comment", {"content": "普通评论"}, format="json")
        comment_pk = resp.data["data"]["pk"]

        perm_name = "deleteComment:SystemApprovalInstance"
        perm = Menu.objects.filter(name=perm_name).first() or menu_factory(
            perm_name, path="api/system/approval-instances/.*/comment/delete$", method="POST"
        )
        approver.roles.add(role)
        role.menu.add(perm)
        api_client.force_authenticate(user=approver)
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/comment/delete", {"pk": comment_pk}, format="json")
        assert resp.status_code == 403

        api_client.force_authenticate(user=superuser)
        resp = api_client.post(f"{INSTANCES_URL}/{instance.pk}/comment/delete", {"pk": comment_pk}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not instance.comments.exists()

    def test_comment_on_missing_instance(self, auth_client):
        resp = auth_client.post(
            f"{INSTANCES_URL}/00000000-0000-0000-0000-000000000000/comment", {"content": "x"}, format="json"
        )
        assert resp.status_code in (403, 404) or resp.data.get("code") != 1000

    def test_detail_includes_comments(self, auth_client, applicant, approver):
        instance = self._make_instance(applicant, approver)
        auth_client.post(f"{INSTANCES_URL}/{instance.pk}/comment", {"content": "详情评论"}, format="json")
        resp = auth_client.get(f"{INSTANCES_URL}/{instance.pk}")
        assert resp.data["code"] == 1000, resp.data
        assert len(resp.data["data"]["comments"]) == 1
        # 列表不带评论（避免 N+1）
        resp = auth_client.get(INSTANCES_URL)
        assert "comments" not in resp.data["data"]["results"][0] or not resp.data["data"]["results"][0]["comments"]
