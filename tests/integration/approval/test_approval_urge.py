# -*- coding: utf-8 -*-
"""人工催办（审批流）：引擎守卫 + 通知投递 + 节流 + API 端点。

口径：
- 仅申请人（或超管）可催办，仅 PENDING 实例；
- 通知对象 = 当前节点待办处理人（无可催对象不占用节流窗口）；
- 节流：同一实例 10 分钟内只发一次（缓存键 approval_flow_urge_{pk}）。
"""

import pytest

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from approval.utils.approval_flow import cancel_instance, create_instance, urge_instance
from system.models import UserInfo

pytestmark = pytest.mark.django_db

URGE_URL = "/api/system/approval-instances/{pk}/urge"


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="urge_applicant", password="Test@123456")


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_user(username="urge_approver", password="Test@123456")


@pytest.fixture
def other(db):
    return UserInfo.objects.create_user(username="urge_other", password="Test@123456")


def make_instance(applicant, approver, code="urge_flow"):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver.username,
    )
    instance, error = create_instance(flow=flow, applicant=applicant, title="催办测试", form_data={})
    assert error is None, error
    return instance


@pytest.fixture
def notify_spy(monkeypatch):
    """捕获引擎通知调用；发起实例时的 submitted 通知在用例内先清空。"""
    calls = []
    monkeypatch.setattr(
        "approval.utils.approval_flow.engine._notify",
        lambda users, event, instance, extra=None: calls.append(
            {"users": list(users), "event": event, "extra": extra, "instance": instance.pk}
        ),
    )
    return calls


def prepare_instance(applicant, approver, notify_spy, code="urge_flow"):
    instance = make_instance(applicant, approver, code=code)
    notify_spy.clear()  # 丢弃发起时的 submitted 通知，只断言催办
    return instance


class TestUrgeEngine:
    def test_urge_notifies_pending_approvers(self, applicant, approver, notify_spy):
        instance = prepare_instance(applicant, approver, notify_spy)
        ok, detail = urge_instance(instance, applicant, "请尽快处理")
        assert ok, detail
        assert len(notify_spy) == 1
        assert notify_spy[0]["event"] == "urge"
        assert [user.pk for user in notify_spy[0]["users"]] == [approver.pk]
        assert notify_spy[0]["extra"] == "请尽快处理"

    def test_urge_throttled_within_window(self, applicant, approver, notify_spy):
        instance = prepare_instance(applicant, approver, notify_spy)
        assert urge_instance(instance, applicant)[0] is True
        ok, detail = urge_instance(instance, applicant)
        assert ok is False
        assert "10" in str(detail)
        assert len(notify_spy) == 1

    def test_urge_requires_applicant(self, applicant, approver, other, notify_spy):
        instance = prepare_instance(applicant, approver, notify_spy)
        ok, detail = urge_instance(instance, other)
        assert ok is False and notify_spy == []

    def test_urge_requires_pending(self, applicant, approver, notify_spy):
        instance = prepare_instance(applicant, approver, notify_spy)
        cancel_instance(instance, applicant)
        notify_spy.clear()  # 丢弃撤回通知，只断言催办未发生
        ok, _detail = urge_instance(instance, applicant)
        assert ok is False and notify_spy == []

    def test_no_target_does_not_consume_throttle(self, applicant, approver, notify_spy):
        instance = prepare_instance(applicant, approver, notify_spy)
        ApprovalNodeTask.objects.filter(instance=instance).update(status=ApprovalNodeTask.Status.CANCELLED)
        ok, detail = urge_instance(instance, applicant)
        assert ok is False
        assert notify_spy == []
        # 恢复正常待办后可立即催办（未被无效调用占用节流窗口）
        ApprovalNodeTask.objects.filter(instance=instance).update(status=ApprovalNodeTask.Status.PENDING)
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
        assert urge_instance(instance, applicant)[0] is True


class TestUrgeApi:
    def test_urge_endpoint(self, auth_client, superuser, approver, notify_spy):
        instance = prepare_instance(superuser, approver, notify_spy, code="urge_api_flow")
        first = auth_client.post(URGE_URL.format(pk=instance.pk), {"message": "请处理"}, format="json")
        assert first.data["code"] == 1000, first.data
        second = auth_client.post(URGE_URL.format(pk=instance.pk), {}, format="json")
        assert second.data["code"] == 1001, second.data
        # 实例保持审批中，待办归属不变
        instance.refresh_from_db()
        assert instance.status == ApprovalInstance.Status.PENDING
