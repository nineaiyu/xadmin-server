# -*- coding: utf-8 -*-
"""审批委托（审批流三期）集成测试：解析矩阵 + CRUD 与校验 + 委托归属越权护栏。"""

import datetime

import pytest
from django.core.cache import cache as django_cache
from django.utils import timezone
from rest_framework.test import APIClient

from approval.models.approval import ApprovalDelegation, ApprovalFlow, ApprovalFlowNode
from approval.services.approval_flow import resolve_assignees
from identity.models import UserInfo, UserRole
from system.models import DataPermission, FieldPermission, Menu, MenuMeta, ModelLabelField

pytestmark = pytest.mark.django_db

DELEGATIONS_URL = "/api/approval/approval-delegations"


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

    def test_delegated_task_records_source(self, applicant, target, agent):
        """委托代审落任务时记录 delegate_from（审批轨迹标注「由 X 代理」）。"""
        from approval.services.approval_flow import create_instance

        flow, _node = make_flow()
        make_delegation(target, agent)
        instance, error = create_instance(flow=flow, applicant=applicant, title="委托代审", form_data={})
        assert error is None, error
        task = instance.tasks.get()
        assert task.assignee_id == agent.pk
        assert task.delegate_from_id == target.pk


class TestDelegationCrud:
    def test_create_and_list(self, auth_client, target, agent):
        make_flow("leave")
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

    def test_unknown_flow_code_rejected(self, auth_client, target, agent):
        """流程范围 fail-closed：错码委托会静默不生效（引擎按 code 匹配流程），写入即拒绝。"""
        now = timezone.now()
        resp = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(agent.pk),
                "start_time": now.isoformat(),
                "end_time": (now + datetime.timedelta(hours=1)).isoformat(),
                "flow_codes": ["ghost_flow"],
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

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

    def test_inactive_delegate_rejected(self, auth_client, target, agent):
        """停用用户不可被配置为代理人：解析会丢弃停用代理人，节点候选可能被清空。"""
        now = timezone.now()
        agent.is_active = False
        agent.save(update_fields=["is_active"])
        resp = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(agent.pk),
                "start_time": now.isoformat(),
                "end_time": (now + datetime.timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_inactive_new_row_may_overlap(self, auth_client, target, agent):
        """未启用的委托模板允许先保存：重叠校验按本行落库后的 is_active 收口。"""
        now = timezone.now()
        make_delegation(target, agent)
        resp = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(agent.pk),
                "start_time": (now + datetime.timedelta(minutes=10)).isoformat(),
                "end_time": (now + datetime.timedelta(hours=2)).isoformat(),
                "is_active": False,
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_activating_overlapping_row_rejected(self, auth_client, target, agent):
        """未启用模板补启用时同样过重叠校验：与既有生效委托重叠即拒绝。"""
        now = timezone.now()
        make_delegation(target, agent)
        created = auth_client.post(
            DELEGATIONS_URL,
            {
                "delegator": str(target.pk),
                "delegate": str(agent.pk),
                "start_time": (now + datetime.timedelta(minutes=10)).isoformat(),
                "end_time": (now + datetime.timedelta(hours=2)).isoformat(),
                "is_active": False,
            },
            format="json",
        )
        assert created.data["code"] == 1000, created.data
        pk = created.data["data"]["pk"]
        resp = auth_client.patch(f"{DELEGATIONS_URL}/{pk}", {"is_active": True}, format="json")
        assert resp.status_code == 400, resp.data


class TestDelegationOwnership:
    """委托归属越权护栏（对抗性用例）。

    生效委托在节点解析时直接替换「待办归属」（引擎只判行存在、不复查建立者），
    非超管若能以他人名义建委托即等于收编他人全部待办；取值域同理必须收敛到本人。

    非超管请求同时受行级/字段级权限收敛（本项目 fail-closed）：用例显式配置
    「全部数据 + 字段白名单」，否则断言会因权限裁剪而失去意义（恒空 / 写忽略）。
    """

    CREATE_PATH = "api/approval/approval-delegations$"
    LIST_PATH = "api/approval/approval-delegations$"
    DETAIL_PATH = r"api/approval/approval-delegations/(?P<pk>[^/.]+)$"
    ALL_PATH = "api/approval/approval-delegations/all$"
    MODEL_LABEL = "approval.approvaldelegation"
    WRITABLE_FIELDS = ("delegator", "delegate", "start_time", "end_time", "flow_codes", "is_active", "remark")

    @pytest.fixture
    def other(self, db):
        return UserInfo.objects.create_user(username="deleg_other", password="Test@123456")

    @pytest.fixture
    def other_agent(self, db):
        return UserInfo.objects.create_user(username="deleg_other_agent", password="Test@123456")

    @pytest.fixture
    def grant_data_all(self, db):
        """行级「全部数据」授权（数据权限默认拒绝：无授权时列表恒空、FK 校验失败）。"""

        def _grant(user, *tables):
            rules = [
                {"table": table, "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}
                for table in tables
            ]
            user.rules.add(DataPermission.objects.create(name=f"全部数据-{user.username}", rules=rules))
            django_cache.clear()

        return _grant

    @pytest.fixture
    def grant_permission(self, db):
        """把权限点（菜单）授予用户角色；fields 非空时同步配置模型字段白名单。

        授权变更后清缓存（权限结果按用户+方法缓存 24h，字段权限 10s）。
        """

        def _grant(user, name, path, method="GET", fields=()):
            role = user.roles.first()
            if role is None:
                role = UserRole.objects.create(name=f"角色-{user.username}", code=f"role-{user.username}")
                user.roles.add(role)
            menu = Menu.objects.create(
                name=name,
                path=path,
                method=method,
                menu_type=Menu.MenuChoices.PERMISSION,
                meta=MenuMeta.objects.create(title=name),
            )
            role.menu.add(menu)
            if fields:
                root = self._ensure_label(self.MODEL_LABEL)
                permission = FieldPermission.objects.create(role=role, menu=menu)
                permission.field.add(*[self._ensure_label(field, root) for field in fields])
            django_cache.clear()
            return menu

        return _grant

    @staticmethod
    def _ensure_label(name, parent=None):
        label = ModelLabelField.objects.filter(
            name=name, parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
        ).first()
        if label is None:
            label = ModelLabelField.objects.create(
                name=name, label=name, parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
            )
        return label

    @staticmethod
    def _client(user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=user)
        return client

    @staticmethod
    def _payload(delegator, delegate):
        now = timezone.now()
        return {
            "delegator": str(delegator.pk),
            "delegate": str(delegate.pk),
            "start_time": (now - datetime.timedelta(minutes=5)).isoformat(),
            "end_time": (now + datetime.timedelta(hours=2)).isoformat(),
        }

    def test_create_for_others_rejected(self, normal_user, other, agent, grant_permission, grant_data_all):
        """以他人名义建委托：拒绝且不落行（否则收编他人待办）。"""
        grant_data_all(normal_user, "identity.userinfo", self.MODEL_LABEL)
        grant_permission(
            normal_user, "create:SystemApprovalDelegation", self.CREATE_PATH, method="POST", fields=self.WRITABLE_FIELDS
        )
        resp = self._client(normal_user).post(DELEGATIONS_URL, self._payload(other, agent), format="json")
        assert resp.status_code == 400, resp.data
        assert not ApprovalDelegation.objects.filter(delegator=other).exists()

    def test_create_in_own_name_allowed(self, normal_user, agent, grant_permission, grant_data_all):
        grant_data_all(normal_user, "identity.userinfo", self.MODEL_LABEL)
        grant_permission(
            normal_user, "create:SystemApprovalDelegation", self.CREATE_PATH, method="POST", fields=self.WRITABLE_FIELDS
        )
        resp = self._client(normal_user).post(DELEGATIONS_URL, self._payload(normal_user, agent), format="json")
        assert resp.data["code"] == 1000, resp.data
        assert ApprovalDelegation.objects.filter(delegator=normal_user, delegate=agent).exists()

    def test_list_scoped_to_own(self, normal_user, other, agent, other_agent, grant_permission, grant_data_all):
        """列表只含本人作为委托人的记录（他人委托记录不可见）。"""
        grant_data_all(normal_user, self.MODEL_LABEL)
        grant_permission(normal_user, "list:SystemApprovalDelegation", self.LIST_PATH, method="GET")
        make_delegation(normal_user, agent)
        make_delegation(other, other_agent)
        resp = self._client(normal_user).get(DELEGATIONS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.json()["data"]["total"] == 1

    def test_management_permission_sees_all(
        self, normal_user, other, agent, other_agent, grant_permission, grant_data_all
    ):
        """「查看全部委托记录」授权后可见全部（管理视角权限点）。"""
        grant_data_all(normal_user, self.MODEL_LABEL)
        grant_permission(normal_user, "list:SystemApprovalDelegation", self.LIST_PATH, method="GET")
        grant_permission(normal_user, "all:SystemApprovalDelegation", self.ALL_PATH, method="GET")
        make_delegation(normal_user, agent)
        make_delegation(other, other_agent)
        resp = self._client(normal_user).get(DELEGATIONS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.json()["data"]["total"] == 2

    def test_superuser_sees_all(self, superuser, other, other_agent):
        make_delegation(other, other_agent)
        resp = self._client(superuser).get(DELEGATIONS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.json()["data"]["total"] == 1

    def test_detail_and_destroy_of_others_hidden(
        self, normal_user, other, other_agent, grant_permission, grant_data_all
    ):
        """他人记录的详情/删除按不可见处理，且不产生副作用。"""
        grant_data_all(normal_user, self.MODEL_LABEL)
        grant_permission(normal_user, "retrieve:SystemApprovalDelegation", self.DETAIL_PATH, method="GET")
        grant_permission(normal_user, "destroy:SystemApprovalDelegation", self.DETAIL_PATH, method="DELETE")
        row = make_delegation(other, other_agent)
        client = self._client(normal_user)
        detail = client.get(f"{DELEGATIONS_URL}/{row.pk}")
        assert detail.status_code in (400, 404), detail.data
        deleted = client.delete(f"{DELEGATIONS_URL}/{row.pk}")
        assert deleted.status_code in (400, 404), deleted.data
        assert ApprovalDelegation.objects.filter(pk=row.pk).exists()

    def test_update_delegator_to_others_rejected(self, normal_user, other, agent, grant_permission, grant_data_all):
        """改委托人也走同一护栏：本人记录不能改成以他人名义。"""
        grant_data_all(normal_user, "identity.userinfo", self.MODEL_LABEL)
        grant_permission(
            normal_user,
            "partialUpdate:SystemApprovalDelegation",
            self.DETAIL_PATH,
            method="PATCH",
            fields=self.WRITABLE_FIELDS,
        )
        row = make_delegation(normal_user, agent)
        resp = self._client(normal_user).patch(
            f"{DELEGATIONS_URL}/{row.pk}", {"delegator": str(other.pk)}, format="json"
        )
        assert resp.status_code == 400, resp.data
        row.refresh_from_db()
        assert row.delegator_id == normal_user.pk

    def test_all_code_cannot_destroy_others(self, normal_user, other, other_agent, grant_permission, grant_data_all):
        """「查看全部委托」只放大读取面：持 all 码删除他人委托 → 403，行保留。

        get_queryset 同时是删除门禁的历史缺陷：all 码可见他人记录后，
        destroy 经 get_object 可硬删他人生效委托（静默拆掉他人代审权）。
        """
        grant_data_all(normal_user, self.MODEL_LABEL)
        grant_permission(normal_user, "list:SystemApprovalDelegation", self.LIST_PATH, method="GET")
        grant_permission(normal_user, "all:SystemApprovalDelegation", self.ALL_PATH, method="GET")
        grant_permission(normal_user, "destroy:SystemApprovalDelegation", self.DETAIL_PATH, method="DELETE")
        row = make_delegation(other, other_agent)

        client = self._client(normal_user)
        assert client.get(f"{DELEGATIONS_URL}").json()["data"]["total"] == 1  # all 码放大读取面生效
        resp = client.delete(f"{DELEGATIONS_URL}/{row.pk}")
        assert resp.status_code == 403, resp.data
        assert ApprovalDelegation.objects.filter(pk=row.pk).exists()

    def test_delegator_destroys_own_delegation(self, normal_user, agent, grant_permission, grant_data_all):
        """销毁护栏不误伤本人：委托人删除自己的委托照常放行。"""
        grant_data_all(normal_user, self.MODEL_LABEL)
        grant_permission(normal_user, "destroy:SystemApprovalDelegation", self.DETAIL_PATH, method="DELETE")
        row = make_delegation(normal_user, agent)

        resp = self._client(normal_user).delete(f"{DELEGATIONS_URL}/{row.pk}")
        assert resp.status_code == 200, resp.data
        assert not ApprovalDelegation.objects.filter(pk=row.pk).exists()


class TestDelegationImpact:
    """停用影响面对账同一实体：流程节点 PENDING 待办（与 _expand_delegations 同源）。

    历史缺陷：impact 查的是敏感操作审批单（ApprovalRequest.current_assignees）——
    委托在流程域的唯一消费点是节点候选人替换，敏感审批链不读委托，
    「停用前列出受影响在途单」的承诺落空。
    """

    @pytest.fixture
    def imp_applicant(self, db):
        return UserInfo.objects.create_user(username="deleg_imp_applicant", password="Test@123456")

    @staticmethod
    def _superuser_client(superuser):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        return client

    @staticmethod
    def _impact_data(client, delegation):
        resp = client.get(f"{DELEGATIONS_URL}/{delegation.pk}/impact")
        assert resp.status_code == 200, resp.data
        return resp.data["data"]

    def _pending_instance(self, applicant):
        from approval.services.approval_flow import create_instance

        flow, _node = make_flow(code="deleg_impact")
        instance, error = create_instance(flow=flow, applicant=applicant, title="委托影响面单", form_data={})
        assert error is None, error
        return instance

    def test_delegate_side_pending_counted(self, superuser, imp_applicant, target, agent):
        """生效委托：待办经委托展开落在代理人名下，impact 计入 delegate_pending。"""
        delegation = make_delegation(target, agent)
        instance = self._pending_instance(imp_applicant)
        task = instance.tasks.first()
        assert task.assignee_id == agent.pk
        assert task.delegate_from_id == target.pk

        data = self._impact_data(self._superuser_client(superuser), delegation)
        assert data["total"] == 1
        assert data["delegator_pending"] == 0
        assert data["delegate_pending"] == 1

    def test_delegator_side_pending_counted(self, superuser, imp_applicant, target, agent):
        """未生效委托：待办挂在委托人名下，impact 计入 delegator_pending。"""
        delegation = make_delegation(target, agent, is_active=False)
        instance = self._pending_instance(imp_applicant)
        assert instance.tasks.first().assignee_id == target.pk

        data = self._impact_data(self._superuser_client(superuser), delegation)
        assert data["total"] == 1
        assert data["delegator_pending"] == 1
        assert data["delegate_pending"] == 0

    def test_sensitive_approval_request_not_counted(self, superuser, imp_applicant, target, agent):
        """敏感操作审批单（ApprovalRequest）不参与影响面：对账实体为流程待办。"""
        from approval.models.approval_request import ApprovalRequest

        delegation = make_delegation(target, agent, is_active=False)
        self._pending_instance(imp_applicant)
        request_row = ApprovalRequest.objects.create(
            module="敏感操作", method="DELETE", path="/api/test/1", creator=imp_applicant, params={}
        )
        request_row.current_assignees.add(target)

        data = self._impact_data(self._superuser_client(superuser), delegation)
        assert data["total"] == 1
        assert data["delegator_pending"] == 1

    def test_finished_instance_tasks_not_counted(self, superuser, imp_applicant, target, agent):
        """仅统计在途实例的待办：终态实例的 PENDING 任务不进影响面。"""
        from approval.models.approval import ApprovalInstance

        delegation = make_delegation(target, agent, is_active=False)
        instance = self._pending_instance(imp_applicant)
        ApprovalInstance.objects.filter(pk=instance.pk).update(status=ApprovalInstance.Status.CANCELLED)

        data = self._impact_data(self._superuser_client(superuser), delegation)
        assert data["total"] == 0
