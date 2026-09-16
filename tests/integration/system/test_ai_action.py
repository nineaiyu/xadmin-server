# -*- coding: utf-8 -*-
"""AI 受限动作（A2）集成测试：草稿 → 确认 → 以用户身份执行 → 审计。

覆盖：
- 灰度开关（默认关闭）：动作草稿与执行均被拒，且不触达 LLM；
- 白名单：未知动作 / 未登记动作类型一律拒绝；
- 参数服务端重校验：LLM 输出按不可信输入处理（非法参数/缺参数拒绝且不落库）；
- 权限双门：仅有 AI 执行端点权限、无底层业务权限点（create:SystemLeave /
  create:FormMySubmission）的用户不得执行；
- 以用户身份执行：creator/申请人恒为发起用户，参数中的替他人字段被忽略（越权矩阵）；
- 审批协议复用：需审批的动态表单首次确认返回 412（code=1002），审批通过后携
  X-Approval-Id 重放才落库；跨用户令牌 / 指纹不一致一律 403；
- 审计：OperationLog(module=AI:action, auth_type=ai)。
"""

import json

import pytest

from message.models import ChatMessage
from system.models import Menu, OperationLog, UserInfo
from system.models.approval import ApprovalRequest
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.models.leave import Leave
from system.utils.approval import approve_request

pytestmark = pytest.mark.django_db

EXECUTE_URL = "/api/system/ai/assistant/action/execute"
CHAT_URL = "/api/chat/ai/message"

LEAVE_PARAMS = {
    "leave_type": "annual",
    "start_date": "2031-05-11",
    "end_date": "2031-05-12",
    "reason": "AI 动作联调",
}


class StubLLM:
    """按序返回预设回答并记录请求的假 LLM（对齐 test_ai_assistant 的桩方式）。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json})
        payload = self.answers.pop(0) if self.answers else ""
        return _FakeResponse({"choices": [{"message": {"content": payload}}]})


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def ai_action_settings(settings):
    """开启 AI 助手 + 动作灰度（默认关闭是另一组用例，不共用）。"""
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    settings.AI_ACTION_ENABLED = True
    return settings


@pytest.fixture
def stub_llm(monkeypatch):
    holder = {}

    def _install(*answers):
        stub = StubLLM(answers)
        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)
        holder["stub"] = stub
        return stub

    holder["install"] = _install
    return holder


def draft_answer(action, params, summary="一句话摘要"):
    return json.dumps({"action": action, "params": params, "summary": summary}, ensure_ascii=False)


def grant_perms(role, menu_factory, perms):
    for name, path, method in perms:
        perm = Menu.objects.filter(name=name).first() or menu_factory(name, path=path, method=method)
        role.menu.add(perm)


AI_EXECUTE_PERM = (("actionExecute:AiAssistant", "api/system/ai/assistant/action/execute$", "POST"),)
CHAT_PERMS = (("ask:ChatRoom", "api/chat/ai/message$", "POST"),)
LEAVE_CREATE_PERMS = (("create:SystemLeave", "api/system/leaves$", "POST"),)
DFORM_CREATE_PERMS = (("create:FormMySubmission", "api/system/dynamic-form-submissions$", "POST"),)


@pytest.fixture
def action_user(db, role, menu_factory):
    """持有 AI 执行端点权限 + 聊天提问权限的普通用户（业务权限按用例追加）。"""
    user = UserInfo.objects.create_user(username="ai_actor", password="Test@123456", nickname="AI执行人")
    user.roles.add(role)
    grant_perms(role, menu_factory, AI_EXECUTE_PERM + CHAT_PERMS)
    return user


@pytest.fixture
def action_client(api_client, action_user):
    api_client.force_authenticate(user=action_user)
    return api_client


@pytest.fixture
def flow_approver(db):
    """请假流程节点指定的审批人（申请人不能自审，引擎 fail-closed 需要真实存在的审批人）。"""
    return UserInfo.objects.create_user(username="leave_approver", password="Test@123456", nickname="主管")


def make_leave_flow(assignee_value="leave_approver"):
    from system.models.approval import ApprovalFlow, ApprovalFlowNode

    flow = ApprovalFlow.objects.create(
        name="请假审批",
        code="leave",
        form_schema=[{"key": "reason", "label": "请假事由", "type": "textarea", "required": True}],
    )
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="直属主管审批",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=assignee_value,
        timeout_hours=24,
    )
    return flow


def make_form(name="E2E-AI动作表单", approval_required=False, schema=None):
    return DynamicForm.objects.create(
        name=name,
        schema=schema
        or {"fields": [{"key": "note", "label": "备注", "type": "input", "required": False, "max_length": 100}]},
        approval_required=approval_required,
        is_active=True,
    )


class TestActionGate:
    """灰度与门禁：默认关闭；AI 未配置同样拒绝。"""

    def test_execute_rejected_when_switch_off(self, auth_client, db, settings):
        settings.AI_ACTION_ENABLED = False
        settings.AI_ASSISTANT_ENABLED = True
        settings.AI_BASE_URL = "https://ai.example.com/v1"
        settings.AI_API_KEY = "sk"
        settings.AI_MODEL = "m"
        response = auth_client.post(EXECUTE_URL, {"action": "leave.submit", "params": LEAVE_PARAMS}, format="json")
        assert response.status_code == 200
        assert response.data["code"] == 1001
        assert not Leave.objects.exists()

    def test_execute_rejected_when_ai_unconfigured(self, auth_client, db, settings):
        settings.AI_ACTION_ENABLED = True
        settings.AI_ASSISTANT_ENABLED = True
        settings.AI_BASE_URL = ""
        response = auth_client.post(EXECUTE_URL, {"action": "leave.submit", "params": LEAVE_PARAMS}, format="json")
        assert response.data["code"] == 1001

    def test_chat_do_rejected_when_switch_off(self, action_client, ai_action_settings, stub_llm):
        ai_action_settings.AI_ACTION_ENABLED = False
        stub = stub_llm["install"]("应当不被调用")
        response = action_client.post(CHAT_URL, {"content": "/do 帮我请年假"}, format="json")
        assert response.data["code"] == 1001
        assert stub.requests == []  # 未触达 LLM


class TestActionWhitelist:
    """白名单与参数校验（LLM 输出按不可信输入处理）。"""

    def test_unknown_action_rejected(self, auth_client, ai_action_settings):
        response = auth_client.post(EXECUTE_URL, {"action": "user.destroy_all", "params": {}}, format="json")
        assert response.data["code"] == 1001
        assert not OperationLog.objects.filter(module="AI:action", status_code=1000).exists()

    def test_param_validation_rejected(self, auth_client, ai_action_settings):
        bad = dict(LEAVE_PARAMS, end_date="2031-05-10")  # 结束早于开始
        response = auth_client.post(EXECUTE_URL, {"action": "leave.submit", "params": bad}, format="json")
        assert response.data["code"] == 1001
        assert not Leave.objects.exists()

    def test_execute_revalidates_client_payload(self, action_client, ai_action_settings, action_user):
        """前端回传参数不可信：缺 reason 也必须被服务端拒绝。"""
        response = action_client.post(
            EXECUTE_URL,
            {"action": "leave.submit", "params": {"leave_type": "annual", "start_date": "2031-05-11"}},
            format="json",
        )
        assert response.data["code"] == 1001


class TestLeaveAction:
    def test_execute_creates_leave_for_current_user(
        self, action_client, ai_action_settings, action_user, menu_factory, flow_approver
    ):
        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        make_leave_flow()
        response = action_client.post(EXECUTE_URL, {"action": "leave.submit", "params": LEAVE_PARAMS}, format="json")
        assert response.data["code"] == 1000
        leave = Leave.objects.get()
        assert str(leave.creator.pk) == str(action_user.pk)
        assert leave.status == Leave.Status.PENDING
        audit = OperationLog.objects.get(module="AI:action")
        assert audit.auth_type == OperationLog.AuthType.AI
        assert audit.status_code == 1000

    def test_identity_guard_ignores_impersonation_params(
        self, action_client, ai_action_settings, action_user, db, menu_factory, flow_approver
    ):
        """越权矩阵：参数里的 creator/creator_id 不得改变归属（无可用审批人时保留草稿仍属本人）。"""
        other = UserInfo.objects.create_user(username="other_guy", password="Test@123456", nickname="别人")
        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        params = dict(LEAVE_PARAMS, creator=other.pk, creator_id=other.pk, applicant=other.username)
        response = action_client.post(EXECUTE_URL, {"action": "leave.submit", "params": params}, format="json")
        assert response.data["code"] == 1000
        leave = Leave.objects.get()
        assert str(leave.creator.pk) == str(action_user.pk)

    def test_requires_business_permission(self, action_client, ai_action_settings, action_user):
        """越权矩阵：有 AI 执行端点权限、无 create:SystemLeave 不得发起请假。"""
        response = action_client.post(EXECUTE_URL, {"action": "leave.submit", "params": LEAVE_PARAMS}, format="json")
        assert response.data["code"] == 1001
        assert not Leave.objects.exists()

    def test_draft_via_chat_command(self, action_client, ai_action_settings, stub_llm, action_user, menu_factory):
        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        stub_llm["install"](draft_answer("leave.submit", LEAVE_PARAMS))
        response = action_client.post(CHAT_URL, {"content": "/do 帮我请 2031-05-11 到 05-12 的年假"}, format="json")
        assert response.data["code"] == 1000
        payload = response.data["data"]["message"]
        assert payload["extra"]["mode"] == "action"
        draft = payload["extra"]["action_draft"]
        assert draft["action"] == "leave.submit"
        assert draft["params"]["start_date"] == "2031-05-11"
        assert draft["requires_approval"] is False
        # 用户消息与 AI 草稿消息均已落库
        assert ChatMessage.objects.filter(extra__mode="action").count() == 1

    def test_draft_clarifies_when_not_executable(self, action_client, ai_action_settings, stub_llm):
        stub_llm["install"](json.dumps({"action": None, "message": "请问请哪种假期类型？"}, ensure_ascii=False))
        response = action_client.post(CHAT_URL, {"content": "/do 我想请假"}, format="json")
        assert response.data["code"] == 1000
        payload = response.data["data"]["message"]
        assert payload["extra"].get("mode") == "chat"
        assert "action_draft" not in payload["extra"]

    def test_draft_rejects_unknown_action(self, action_client, ai_action_settings, stub_llm):
        stub_llm["install"](draft_answer("user.destroy_all", {}))
        response = action_client.post(CHAT_URL, {"content": "/do 删库"}, format="json")
        assert response.data["code"] == 1001
        # 降级为 system 消息（前端可见），不产生动作草稿
        assert ChatMessage.objects.filter(extra__error=True).exists()
        assert not ChatMessage.objects.filter(extra__mode="action").exists()

    def test_draft_rejects_without_business_permission(self, action_client, ai_action_settings, stub_llm):
        """越权矩阵：无 create:SystemLeave 的用户连草稿都拿不到（fail-closed）。"""
        stub_llm["install"](draft_answer("leave.submit", LEAVE_PARAMS))
        response = action_client.post(CHAT_URL, {"content": "/do 请年假"}, format="json")
        assert response.data["code"] == 1001


class TestDformAction:
    def test_submit_without_approval(self, action_client, ai_action_settings, action_user, menu_factory):
        grant_perms(action_user.roles.first(), menu_factory, DFORM_CREATE_PERMS)
        form = make_form()
        response = action_client.post(
            EXECUTE_URL,
            {"action": "dform.submit", "params": {"form_id": str(form.pk), "data": {"note": "AI 提交"}}},
            format="json",
        )
        assert response.data["code"] == 1000
        submission = DynamicFormSubmission.objects.get()
        assert str(submission.creator.pk) == str(action_user.pk)
        assert submission.data["note"] == "AI 提交"

    def test_unavailable_when_no_active_form(self, action_client, ai_action_settings):
        response = action_client.post(
            EXECUTE_URL, {"action": "dform.submit", "params": {"form_id": "x", "data": {}}}, format="json"
        )
        assert response.data["code"] == 1001

    def test_inactive_form_rejected(self, action_client, ai_action_settings, action_user, menu_factory):
        grant_perms(action_user.roles.first(), menu_factory, DFORM_CREATE_PERMS)
        form = make_form()
        form.is_active = False
        form.save(update_fields=["is_active"])
        response = action_client.post(
            EXECUTE_URL,
            {"action": "dform.submit", "params": {"form_id": str(form.pk), "data": {}}},
            format="json",
        )
        assert response.data["code"] == 1001
        assert not DynamicFormSubmission.objects.exists()

    def test_approval_protocol_replay(self, action_client, ai_action_settings, action_user, superuser, menu_factory):
        """412 协议闭环：首次确认建单 → 审批通过 → 携令牌重放 → 落库（一次性）。"""
        grant_perms(action_user.roles.first(), menu_factory, DFORM_CREATE_PERMS)
        form = make_form(approval_required=True)
        body = {"action": "dform.submit", "params": {"form_id": str(form.pk), "data": {"note": "待审批"}}}

        first = action_client.post(EXECUTE_URL, body, format="json")
        assert first.status_code == 412
        assert first.data["code"] == 1002
        assert first.data["type"] == "approval_required"
        approval_id = first.data["data"]["approval_id"]
        assert not DynamicFormSubmission.objects.exists()

        approval = ApprovalRequest.objects.get(pk=approval_id)
        assert str(approval.creator.pk) == str(action_user.pk)
        ok, __ = approve_request(approval, superuser)
        assert ok

        second = action_client.post(EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=approval_id)
        assert second.data["code"] == 1000
        assert DynamicFormSubmission.objects.count() == 1

        third = action_client.post(EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=approval_id)
        assert third.status_code == 403

    def test_cross_user_token_rejected(
        self, api_client, action_client, ai_action_settings, action_user, superuser, db, menu_factory
    ):
        """越权矩阵：他人不能携带（或消费）我的审批令牌（令牌绑定申请人）。"""
        grant_perms(action_user.roles.first(), menu_factory, DFORM_CREATE_PERMS)
        form = make_form(approval_required=True)
        body = {"action": "dform.submit", "params": {"form_id": str(form.pk), "data": {"note": "x"}}}
        first = action_client.post(EXECUTE_URL, body, format="json")
        assert first.status_code == 412

        other = UserInfo.objects.create_user(username="ai_other", password="Test@123456", nickname="另一个执行人")
        role = action_user.roles.first()
        other.roles.add(role)
        grant_perms(role, menu_factory, AI_EXECUTE_PERM + DFORM_CREATE_PERMS)
        other_client = api_client
        other_client.force_authenticate(user=other)
        stolen = other_client.post(
            EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=first.data["data"]["approval_id"]
        )
        assert stolen.status_code == 403
        assert not DynamicFormSubmission.objects.exists()


class TestActionResultRoom:
    def test_result_pushed_to_room(self, action_client, ai_action_settings, action_user, menu_factory, flow_approver):
        from message import chat as chat_service

        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        make_leave_flow()
        room = chat_service.get_or_create_ai_room(action_user)
        response = action_client.post(
            EXECUTE_URL,
            {"action": "leave.submit", "params": LEAVE_PARAMS, "room_id": room.pk},
            format="json",
        )
        assert response.data["code"] == 1000
        system_messages = ChatMessage.objects.filter(room=room, message_type=ChatMessage.MessageType.SYSTEM)
        assert system_messages.count() == 1
        assert system_messages.first().extra["mode"] == "action"

    def test_room_id_of_other_user_ignored(self, auth_client, ai_action_settings, superuser, action_user, menu_factory):
        from message import chat as chat_service

        room = chat_service.get_or_create_ai_room(action_user)
        response = auth_client.post(
            EXECUTE_URL,
            {"action": "leave.submit", "params": LEAVE_PARAMS, "room_id": room.pk},
            format="json",
        )
        # superuser 走 leave 流程缺审批人时也允许（结果以 detail 表达），但不得写入他人房间
        assert response.data["code"] in (1000, 1001)
        assert not ChatMessage.objects.filter(room=room, message_type=ChatMessage.MessageType.SYSTEM).exists()
