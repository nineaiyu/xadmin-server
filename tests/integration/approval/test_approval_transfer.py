# -*- coding: utf-8 -*-
"""审批转交 + 加签节点语义 + 管理视角（scope=ongoing）。

口径：
- 转交：处理人本人（或超管）把当前待办转给他人；原任务 CANCELLED 留痕（comment 注明
  转交给谁），新任务 delegate_from=原处理人（时间线标注来源）；通知新处理人（transferred）；
- 加签：仅会签（AND）/比例会签（RATIO）节点可用——会签下新候选必须通过、比例会签下
  达标线只收紧不放松；或签（OR）节点明确拒绝（任一通过即流转，加签无约束力）；
- 管理视角：scope=ongoing 列出全部审批中的申请，需 ongoing:SystemApprovalInstance
  权限点（超管与运行时访问控制同口径豁免）。
"""

import pytest
from django.core.cache import cache as django_cache
from django.utils.translation import gettext as _
from rest_framework.test import APIClient

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from approval.utils.approval_flow import add_sign, create_instance, transfer_task
from system.models import Menu, MenuMeta, UserInfo, UserRole

pytestmark = pytest.mark.django_db

TRANSFER_PATH = r"api/system/approval-instances/(?P<pk>[^/.]+)/transfer$"
ADD_SIGN_PATH = r"api/system/approval-instances/(?P<pk>[^/.]+)/add-sign$"
ONGOING_PATH = "api/system/approval-instances/ongoing$"
TRANSFER_URL = "/api/system/approval-instances/{pk}/transfer"
ADD_SIGN_URL = "/api/system/approval-instances/{pk}/add-sign"
LIST_URL = "/api/system/approval-instances"


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="transfer_applicant", password="Test@123456")


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_user(username="transfer_approver", password="Test@123456")


@pytest.fixture
def target(db):
    return UserInfo.objects.create_user(username="transfer_target", password="Test@123456")


@pytest.fixture
def outsider(db):
    return UserInfo.objects.create_user(username="transfer_outsider", password="Test@123456")


@pytest.fixture
def grant_permission(db):
    """把权限点（菜单）授予用户角色并失效权限缓存。

    ``get_user_permission`` 带 24h 缓存：授权变更后必须清缓存再取（项目既有口径）。
    """

    def _grant(user, name: str, path: str, method: str = "POST"):
        role = user.roles.first()
        if role is None:
            role = UserRole.objects.create(name=f"角色-{user.username}", code=f"role-{user.username}")
            user.roles.add(role)
        meta = MenuMeta.objects.create(title=name)
        menu = Menu.objects.create(
            name=name,
            path=path,
            method=method,
            menu_type=Menu.MenuChoices.PERMISSION,
            meta=meta,
        )
        role.menu.add(menu)
        django_cache.clear()
        return menu

    return _grant


def make_instance(applicant, approver, code="transfer_flow", approve_type=ApprovalFlowNode.ApproveType.AND):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=approve_type,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver.username,
    )
    instance, error = create_instance(flow=flow, applicant=applicant, title="转交测试", form_data={})
    assert error is None, error
    return instance


@pytest.fixture
def notify_spy(monkeypatch):
    """捕获引擎通知调用（extra_actions 经 engine 模块属性访问，单一 patch 点）。"""
    calls = []
    monkeypatch.setattr(
        "approval.utils.approval_flow.engine._notify",
        lambda users, event, instance, extra=None: calls.append(
            {"users": list(users), "event": event, "extra": extra, "instance": instance.pk}
        ),
    )
    return calls


class TestTransferEngine:
    def test_transfer_reassigns_task(self, applicant, approver, target, notify_spy):
        instance = make_instance(applicant, approver, code="transfer_basic")
        old_task = instance.tasks.first()
        ok, detail = transfer_task(old_task.pk, approver, target.username, "我出差，请代处理")
        assert ok, detail

        old_task.refresh_from_db()
        assert old_task.status == ApprovalNodeTask.Status.CANCELLED
        assert old_task.comment  # 转交留痕（文案随语言环境，不做字面耦合）

        new_task = instance.tasks.exclude(pk=old_task.pk).get()
        assert new_task.status == ApprovalNodeTask.Status.PENDING
        assert new_task.assignee_id == target.pk
        assert new_task.delegate_from_id == approver.pk
        assert new_task.comment == "我出差，请代处理"

    def test_transfer_notifies_target(self, applicant, approver, target, notify_spy):
        instance = make_instance(applicant, approver, code="transfer_notify")
        notify_spy.clear()
        ok, _detail = transfer_task(instance.tasks.first().pk, approver, target.username)
        assert ok
        assert [call["event"] for call in notify_spy] == ["transferred"]
        assert [user.pk for user in notify_spy[0]["users"]] == [target.pk]

    def test_transfer_requires_assignee(self, applicant, approver, target, outsider):
        instance = make_instance(applicant, approver, code="transfer_guard")
        ok, detail = transfer_task(instance.tasks.first().pk, outsider, target.username)
        assert ok is False
        assert detail  # 有可读原因

    def test_superuser_can_transfer_others(self, applicant, approver, target, superuser):
        instance = make_instance(applicant, approver, code="transfer_super")
        ok, detail = transfer_task(instance.tasks.first().pk, superuser, target.username)
        assert ok, detail
        assert instance.tasks.filter(assignee=target, status=ApprovalNodeTask.Status.PENDING).exists()

    def test_transfer_rejects_applicant_target(self, applicant, approver, notify_spy):
        instance = make_instance(applicant, approver, code="transfer_applicant_target")
        notify_spy.clear()
        ok, _detail = transfer_task(instance.tasks.first().pk, approver, applicant.username)
        assert ok is False
        assert all(call["event"] != "transferred" for call in notify_spy)

    def test_transfer_rejects_same_assignee(self, applicant, approver):
        instance = make_instance(applicant, approver, code="transfer_same")
        ok, _detail = transfer_task(instance.tasks.first().pk, approver, approver.username)
        assert ok is False

    def test_transfer_processed_task_fails(self, applicant, approver, target):
        from approval.utils.approval_flow import approve_task

        instance = make_instance(applicant, approver, code="transfer_processed")
        task = instance.tasks.first()
        assert approve_task(task.pk, approver)[0] is True
        ok, _detail = transfer_task(task.pk, approver, target.username)
        assert ok is False

    def test_transfer_disabled_target_fails(self, applicant, approver, target):
        instance = make_instance(applicant, approver, code="transfer_disabled")
        target.is_active = False
        target.save(update_fields=["is_active"])
        ok, _detail = transfer_task(instance.tasks.first().pk, approver, target.username)
        assert ok is False

    def test_transfer_does_not_change_ratio_required(self, applicant, approver, target):
        """RATIO 节点：转交只是换人，不改变候选总数与达标线。"""
        instance = make_instance(
            applicant, approver, code="transfer_ratio", approve_type=ApprovalFlowNode.ApproveType.RATIO
        )
        node = instance.current_node
        old_task = instance.tasks.first()
        ok, detail = transfer_task(old_task.pk, approver, target.username)
        assert ok, detail
        node_tasks = ApprovalNodeTask.objects.filter(instance=instance, node=node)
        assert node_tasks.count() == 2  # 原任务（CANCELLED）+ 新任务（PENDING）
        assert node_tasks.filter(status=ApprovalNodeTask.Status.PENDING).count() == 1
        assert instance.status == ApprovalInstance.Status.PENDING


class TestAddSignNodeType:
    def test_add_sign_rejected_on_or_node(self, applicant, approver, target, notify_spy):
        """或签节点加签无约束力 → 明确拒绝并引导使用转交。"""
        instance = make_instance(applicant, approver, code="addsign_or", approve_type=ApprovalFlowNode.ApproveType.OR)
        notify_spy.clear()
        ok, detail = add_sign(instance, approver, target.username)
        assert ok is False
        assert detail
        assert notify_spy == []
        assert not instance.tasks.filter(assignee=target).exists()

    def test_add_sign_allowed_on_and_node(self, applicant, approver, target, notify_spy):
        instance = make_instance(applicant, approver, code="addsign_and", approve_type=ApprovalFlowNode.ApproveType.AND)
        notify_spy.clear()
        ok, detail = add_sign(instance, approver, target.username)
        assert ok, detail
        added = instance.tasks.get(assignee=target)
        assert added.is_added is True
        assert [call["event"] for call in notify_spy] == ["added"]

    def test_add_sign_allowed_on_ratio_node(self, applicant, approver, target):
        instance = make_instance(
            applicant, approver, code="addsign_ratio", approve_type=ApprovalFlowNode.ApproveType.RATIO
        )
        ok, detail = add_sign(instance, approver, target.username)
        assert ok, detail
        assert instance.tasks.filter(assignee=target, is_added=True).exists()


class TestTransferApi:
    def test_transfer_endpoint(self, applicant, approver, target, grant_permission):
        instance = make_instance(applicant, approver, code="transfer_api")
        grant_permission(approver, "transfer:SystemApprovalInstance", TRANSFER_PATH)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(
            TRANSFER_URL.format(pk=instance.pk),
            {"username": target.username, "comment": "代处理"},
            format="json",
        )
        assert response.data["code"] == 1000, response.data
        assert instance.tasks.filter(assignee=target, status=ApprovalNodeTask.Status.PENDING).exists()

    def test_transfer_endpoint_rejects_non_participant(self, applicant, approver, target, outsider, grant_permission):
        """有权限点但不在可见域（非参与人）：按不可见处理（404，不泄露实例是否存在）。"""
        instance = make_instance(applicant, approver, code="transfer_api_guard")
        grant_permission(outsider, "transfer:SystemApprovalInstance", TRANSFER_PATH)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=outsider)
        response = client.post(TRANSFER_URL.format(pk=instance.pk), {"username": target.username}, format="json")
        # 不可见实例按「不存在」处理：项目异常处理器把 DRF 404 归一为 400（不泄露存在性）
        assert response.status_code in (400, 404)
        assert response.data.get("code") != 1000

    def test_transfer_endpoint_without_permission_denied(self, applicant, approver, target):
        """无权限点：运行时访问控制直接 403。"""
        instance = make_instance(applicant, approver, code="transfer_api_403")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(TRANSFER_URL.format(pk=instance.pk), {"username": target.username}, format="json")
        assert response.status_code == 403

    def test_add_sign_or_node_via_api(self, applicant, approver, target, grant_permission):
        instance = make_instance(
            applicant, approver, code="addsign_api_or", approve_type=ApprovalFlowNode.ApproveType.OR
        )
        grant_permission(approver, "addSign:SystemApprovalInstance", ADD_SIGN_PATH)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(ADD_SIGN_URL.format(pk=instance.pk), {"usernames": target.username}, format="json")
        assert response.data["code"] == 1001, response.data


class TestOngoingScopeApi:
    """管理视角（scope=ongoing）：权限点门控 + 仅返回审批中的实例。"""

    def test_ongoing_requires_permission(self, applicant, approver, normal_user):
        instance = make_instance(applicant, approver, code="ongoing_guard")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.get(f"{LIST_URL}?scope=ongoing")
        assert response.status_code == 403
        assert instance.pk  # 数据存在但越权不可见

    def test_ongoing_visible_with_permission(self, applicant, approver, normal_user, grant_permission):
        instance = make_instance(applicant, approver, code="ongoing_ok")
        # 列表端点本身仍需 list 权限点；ongoing 是叠加的管理视角授权
        grant_permission(normal_user, "list:SystemApprovalInstance", "api/system/approval-instances$", method="GET")
        grant_permission(normal_user, "ongoing:SystemApprovalInstance", ONGOING_PATH, method="GET")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.get(f"{LIST_URL}?scope=ongoing")
        assert response.status_code == 200, response.data
        # 行内容受字段权限裁剪（该用户未配字段白名单 → 行字段为空），这里按分页 total 口径断言
        assert response.json()["data"]["total"] == 1

        # 只含审批中：终止（撤回）后不再出现
        from approval.utils.approval_flow import cancel_instance

        cancel_instance(instance, applicant)
        response = client.get(f"{LIST_URL}?scope=ongoing")
        assert response.json()["data"]["total"] == 0

    def test_ongoing_superuser_exempt(self, superuser, applicant, approver):
        instance = make_instance(applicant, approver, code="ongoing_super")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        response = client.get(f"{LIST_URL}?scope=ongoing")
        assert response.status_code == 200, response.data
        pks = {row["pk"] for row in response.json()["data"]["results"]}
        assert str(instance.pk) in pks

    def test_batch_transfer_two_instances(self, applicant, approver, target, grant_permission):
        """批量转交：两条待办一次交给同一人（逐条独立，全成功）"""
        grant_permission(
            approver, "batchTransfer:SystemApprovalInstance", r"api/system/approval-instances/batch-transfer$"
        )
        first = make_instance(applicant, approver, code="batch_two_a")
        second = make_instance(applicant, approver, code="batch_two_b")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(
            f"{LIST_URL}/batch-transfer",
            {"pks": [str(first.pk), str(second.pk)], "username": target.username, "comment": "批量转交"},
            format="json",
        )
        assert response.status_code == 200, response.data
        body = response.json()
        assert body["code"] == 1000
        assert body["data"]["success"] == 2
        assert body["data"]["failures"] == []
        for instance in (first, second):
            assert instance.tasks.filter(
                assignee=target, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            ).exists()

    def test_batch_transfer_partial_failure(self, applicant, approver, target, outsider, grant_permission):
        """混入非我待办的实例：可转的照转，失败项带明细（不整体拒绝）"""
        grant_permission(
            approver, "batchTransfer:SystemApprovalInstance", r"api/system/approval-instances/batch-transfer$"
        )
        mine = make_instance(applicant, approver, code="batch_partial_a")
        # 审批人是 outsider 的实例：approver 对它没有待办
        other = make_instance(applicant, outsider, code="batch_partial_b")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(
            f"{LIST_URL}/batch-transfer",
            {"pks": [str(mine.pk), str(other.pk)], "username": target.username},
            format="json",
        )
        body = response.json()
        assert body["code"] == 1000
        assert body["data"]["success"] == 1
        assert [item["pk"] for item in body["data"]["failures"]] == [str(other.pk)]
        assert body["data"]["failures"][0]["detail"]

    def test_batch_transfer_all_fail_returns_business_error(
        self, applicant, approver, target, outsider, grant_permission
    ):
        """全部失败：整体业务失败（1001）并把明细带回，前端可直接提示首条原因"""
        grant_permission(
            approver, "batchTransfer:SystemApprovalInstance", r"api/system/approval-instances/batch-transfer$"
        )
        other = make_instance(applicant, outsider, code="batch_allfail")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(
            f"{LIST_URL}/batch-transfer", {"pks": [str(other.pk)], "username": target.username}, format="json"
        )
        body = response.json()
        assert body["code"] == 1001
        assert body["detail"]
        assert len(body["data"]["failures"]) == 1

    def test_batch_transfer_rejects_invisible_instance(self, applicant, approver, target, outsider, grant_permission):
        """越权 pk（我不参与、非我发起）：计失败且不泄露存在性文案之外的细节"""
        grant_permission(
            approver, "batchTransfer:SystemApprovalInstance", r"api/system/approval-instances/batch-transfer$"
        )
        hidden = make_instance(applicant, outsider, code="batch_hidden")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=approver)
        response = client.post(
            f"{LIST_URL}/batch-transfer", {"pks": [str(hidden.pk)], "username": target.username}, format="json"
        )
        assert response.json()["code"] == 1001
        assert hidden.tasks.filter(assignee=target).exists() is False

    def test_current_assignees_exposed(self, superuser, applicant, approver):
        instance = make_instance(applicant, approver, code="ongoing_assignees")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        row = next(
            item
            for item in client.get(f"{LIST_URL}?scope=ongoing").json()["data"]["results"]
            if item["pk"] == str(instance.pk)
        )
        assert approver.username in row["current_assignees"]


class TestInstanceExport:
    """审批导出：复用 list 链路（支持筛选），走轻量序列化器（不含 tasks/表单快照）。

    视角用超管：普通用户导出受**字段权限**裁剪（未配置模型字段权限的角色看不到任何列，
    属既有机制，见 test_serializer_field_permission），会掩盖导出本身的断言。
    """

    def test_export_csv_contains_row(self, superuser, applicant, approver):
        make_instance(applicant, approver, code="export_csv")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        response = client.get(f"{LIST_URL}/export-data?type=csv")
        assert response.status_code == 200, response.content[:300]
        content = response.content.decode("utf-8-sig")
        # 解包占位不用 `_`（函数内赋值会让 gettext 别名 `_` 变成局部变量而不可调用）
        header, _sep, first_row = content.partition("\r\n")
        assert "转交测试" in content  # 实例标题进入导出文件
        assert "csv" in response["Content-Type"]
        # 轻量序列化器：不含任务轨迹与表单快照字段
        assert "tasks" not in header and "form_data" not in header
        # 表头 label 经 gettext 渲染（本机有 .mo 显中文、CI 无 .mo 显英文）：
        # 与产文同源取值，不写死任一语言字面量（写死会跨环境假红，历史教训）
        assert f"{_('Current approvers')}(current_assignees)" in header
        assert first_row  # 至少一行数据

    def test_export_xlsx_default(self, superuser, applicant, approver):
        make_instance(applicant, approver, code="export_xlsx")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        response = client.get(f"{LIST_URL}/export-data")
        assert response.status_code == 200
        # xlsx 是 zip 容器：首字节为 PK
        assert response.content[:2] == b"PK"
        assert "xlsx" in response["Content-Type"]

    def test_export_requires_permission(self, applicant, approver):
        """普通用户无 exportData 权限点：拒绝（导出与列表同受权限点约束）"""
        make_instance(applicant, approver, code="export_denied")
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=applicant)
        response = client.get(f"{LIST_URL}/export-data?type=csv")
        assert response.status_code in (403, 401)


class TestNodeProgress:
    """比例会签达标线预览：node_progress 与 engine 判定同源（加签后达标线抬升）。"""

    def _ratio_instance(self, applicant, approvers, ratio):
        flow = ApprovalFlow.objects.create(
            name=f"流程-ratio-{ratio}", code=f"ratio_{ratio}", form_schema=[], is_active=True
        )
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="比率审批",
            order=1,
            approve_type=ApprovalFlowNode.ApproveType.RATIO,
            approve_ratio=ratio,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=",".join(user.username for user in approvers),
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="比率测试", form_data={})
        assert error is None, error
        return instance

    def _progress(self, client, instance):
        row = client.get(f"{LIST_URL}/{instance.pk}").json()["data"]
        return row["node_progress"]

    def test_ratio_required_matches_engine(self, superuser, applicant, approver, target):
        instance = self._ratio_instance(applicant, [approver, target], 60)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        progress = self._progress(client, instance)
        assert progress["approve_type"] == ApprovalFlowNode.ApproveType.RATIO
        assert progress["total"] == 2
        assert progress["required"] == 2  # ceil(2 × 60%) = 2（与 engine 同口径）
        assert progress["approved"] == 0 and progress["pending"] == 2
        assert progress["reached"] is False

    def test_add_sign_raises_threshold(self, superuser, applicant, approver, target, outsider):
        """加签抬升达标线：2 人 → 4 人时 required 由 2 升到 3（ceil(4 × 60%)）"""
        instance = self._ratio_instance(applicant, [approver, target], 60)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        assert self._progress(client, instance)["required"] == 2

        ok, detail = add_sign(instance, superuser, outsider.username, "补充一位审批人")
        assert ok, detail
        instance.refresh_from_db()
        progress = self._progress(client, instance)
        assert progress["total"] == 3
        assert progress["required"] == 2  # ceil(3 × 60%) = 2

        extra = UserInfo.objects.create_user(username="ratio_extra", password="Test@123456")
        ok, detail = add_sign(instance, superuser, extra.username)
        assert ok, detail
        instance.refresh_from_db()
        progress = self._progress(client, instance)
        assert progress["total"] == 4
        assert progress["required"] == 3  # ceil(4 × 60%) = 3（加签抬高）

    def test_and_and_or_thresholds(self, superuser, applicant, approver, target):
        # 多人 AND 节点：required == 候选总数（全员通过才流转）
        flow = ApprovalFlow.objects.create(name="流程-and", code="progress_and", form_schema=[], is_active=True)
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="会签",
            order=1,
            approve_type=ApprovalFlowNode.ApproveType.AND,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=f"{approver.username},{target.username}",
        )
        and_instance, error = create_instance(flow=flow, applicant=applicant, title="会签测试", form_data={})
        assert error is None, error
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        and_progress = self._progress(client, and_instance)
        assert and_progress["total"] == 2
        assert and_progress["required"] == 2  # 会签需全部通过

        flow = ApprovalFlow.objects.create(name="流程-or", code="progress_or", form_schema=[], is_active=True)
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="或签",
            order=1,
            approve_type=ApprovalFlowNode.ApproveType.OR,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=f"{approver.username},{target.username}",
        )
        or_instance, error = create_instance(flow=flow, applicant=applicant, title="或签测试", form_data={})
        assert error is None, error
        progress = self._progress(client, or_instance)
        assert progress["total"] == 2
        assert progress["required"] == 1  # 或签任一通过即可
        assert progress["approve_type"] == ApprovalFlowNode.ApproveType.OR

    def test_finished_instance_has_no_progress(self, superuser, applicant, approver):
        instance = make_instance(applicant, approver, code="progress_done")
        instance.status = ApprovalInstance.Status.APPROVED
        instance.save(update_fields=["status"])
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=superuser)
        assert self._progress(client, instance) is None
