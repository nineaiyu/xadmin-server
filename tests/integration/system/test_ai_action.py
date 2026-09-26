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

from approval.models.approval import ApprovalRequest
from approval.models.leave import Leave
from approval.utils.approval import approve_request
from message.models import ChatMessage
from system.models import Menu, OperationLog, UserInfo
from system.models.ai import AiChatMessage
from system.models.dform import DynamicForm, DynamicFormSubmission

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


AI_EXECUTE_PERM = (
    ("actionExecute:AiAssistant", "api/system/ai/assistant/action/(interpret(/stream)?|execute)$", "POST"),
)
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
    from approval.models.approval import ApprovalFlow, ApprovalFlowNode

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

    def test_draft_structured_max_tokens(self, action_client, ai_action_settings, stub_llm, action_user, menu_factory):
        """未配置 max_tokens 时 /do 草稿调用携带结构化安全上限（防思考型模型无界推理）。"""
        from system.utils.ai import ai_structured_max_tokens

        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        stub = stub_llm["install"](draft_answer("leave.submit", LEAVE_PARAMS))
        action_client.post(CHAT_URL, {"content": "/do 请年假"}, format="json")
        assert stub.requests[0]["json"]["max_tokens"] == ai_structured_max_tokens()

    def test_draft_structured_max_tokens_configurable(
        self, action_client, ai_action_settings, stub_llm, action_user, menu_factory
    ):
        """AI_STRUCTURED_MAX_TOKENS 可配置（Setting 热更新）：思考型模型调大预算后生效。"""
        grant_perms(action_user.roles.first(), menu_factory, LEAVE_CREATE_PERMS)
        ai_action_settings.AI_STRUCTURED_MAX_TOKENS = 4096
        stub = stub_llm["install"](draft_answer("leave.submit", LEAVE_PARAMS))
        action_client.post(CHAT_URL, {"content": "/do 请年假"}, format="json")
        assert stub.requests[0]["json"]["max_tokens"] == 4096


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


NOTICE_PERMS = (("announcement:SystemNotice", "api/notifications/notice-messages/announcement$", "POST"),)
USER_UPDATE_PERMS = (("partialUpdate:UserInfo", "api/system/user/(?P<pk>[^/.]+)$", "PATCH"),)
NOTICE_PARAMS = {"title": "系统维护公告", "message": "大家好，系统将于今晚 23:00 维护。", "level": "info"}


class TestNoticeAction:
    """发全体公告（**声明式动作**：复用公告管理页 announcement 端点）。

    这里没有 AI 专用业务代码——权限链、序列化校验、内容净化、发布推送全部由
    NoticeMessageViewSet/AnnouncementSerializer 承担；动作只声明 method+path+参数。
    """

    def test_execute_publishes_announcement(self, auth_client, ai_action_settings):
        from notifications.models.message import MessageContent

        response = auth_client.post(EXECUTE_URL, {"action": "notice.publish", "params": NOTICE_PARAMS}, format="json")
        assert response.data["code"] == 1000, response.data
        notice = MessageContent.objects.get()
        assert notice.notice_type == MessageContent.NoticeChoices.NOTICE
        assert notice.publish is True
        assert notice.title == NOTICE_PARAMS["title"]
        assert notice.message == NOTICE_PARAMS["message"]
        audit = OperationLog.objects.get(module="AI:action")
        assert audit.status_code == 1000
        assert audit.auth_type == OperationLog.AuthType.AI

    def test_requires_business_permission(self, action_client, ai_action_settings, action_user):
        """越权矩阵：无 announcement:SystemNotice 不得发公告（预检 + 视图权限链双门）。"""
        from notifications.models.message import MessageContent

        response = action_client.post(EXECUTE_URL, {"action": "notice.publish", "params": NOTICE_PARAMS}, format="json")
        assert response.data["code"] == 1001
        assert not MessageContent.objects.exists()

    def test_validation_rejects_bad_params(self, auth_client, ai_action_settings):
        """参数校验由被复用接口的序列化器接管：缺标题/缺正文/非法级别一律拒绝且不落库。"""
        from notifications.models.message import MessageContent

        bad_params = [
            {"message": "只有正文"},
            {"title": "只有标题"},
            {"title": "标题", "message": "正文", "level": "bogus"},
        ]
        for params in bad_params:
            response = auth_client.post(EXECUTE_URL, {"action": "notice.publish", "params": params}, format="json")
            assert response.data["code"] == 1001, params
        assert not MessageContent.objects.exists()

    def test_catalog_hides_const_fields(self, action_user, menu_factory):
        """动作目录（进 LLM prompt）不下发 const 固定值字段（notice_type 等由服务端注入）。"""
        from system.utils.ai_actions import build_catalog

        grant_perms(action_user.roles.first(), menu_factory, NOTICE_PERMS)
        catalog = build_catalog(action_user)
        entry = next(item for item in catalog["actions"] if item["action"] == "notice.publish")
        assert "title" in entry["params"] and "notice_type" not in entry["params"]

    def test_draft_via_chat_command_and_execute(self, auth_client, ai_action_settings, stub_llm):
        """全链路：/do 草稿 → 确认卡片载荷 → 执行落库。"""
        from notifications.models.message import MessageContent

        stub_llm["install"](draft_answer("notice.publish", NOTICE_PARAMS))
        response = auth_client.post(CHAT_URL, {"content": "/do 发个全体公告，内容是大家好"}, format="json")
        assert response.data["code"] == 1000, response.data
        draft = response.data["data"]["message"]["extra"]["action_draft"]
        assert draft["action"] == "notice.publish"
        assert draft["params"]["title"] == NOTICE_PARAMS["title"]

        executed = auth_client.post(EXECUTE_URL, {"action": draft["action"], "params": draft["params"]}, format="json")
        assert executed.data["code"] == 1000, executed.data
        assert MessageContent.objects.filter(notice_type=MessageContent.NoticeChoices.NOTICE).exists()

    def test_draft_rejects_without_permission(self, action_client, ai_action_settings, stub_llm):
        """无公告权限：连草稿都拿不到（fail-closed）。"""
        stub_llm["install"](draft_answer("notice.publish", NOTICE_PARAMS))
        response = action_client.post(CHAT_URL, {"content": "/do 发公告"}, format="json")
        assert response.data["code"] == 1001


class TestUserSetActiveAction:
    """启用/禁用用户（**声明式动作**：复用 PATCH /api/system/user/<pk>）。

    新增能力只加了「一条声明」：没有 _validate_xxx/_execute_xxx——这正是本轮重构
    的目的（能力扩展不再写 AI 专用业务代码）。
    """

    def test_disable_then_enable_user(self, auth_client, ai_action_settings):
        from system.models import UserInfo

        target = UserInfo.objects.create_user(username="ai_target", password="Test@123456", nickname="目标用户")
        disabled = auth_client.post(
            EXECUTE_URL, {"action": "user.set_active", "params": {"pk": "ai_target", "is_active": False}}, format="json"
        )
        assert disabled.data["code"] == 1000, disabled.data
        target.refresh_from_db()
        assert target.is_active is False

        # 用户名/昵称/主键三种写法都能解析（幂等：确认卡片回传主键同样可执行）
        enabled = auth_client.post(
            EXECUTE_URL, {"action": "user.set_active", "params": {"pk": "目标用户", "is_active": True}}, format="json"
        )
        assert enabled.data["code"] == 1000, enabled.data
        target.refresh_from_db()
        assert target.is_active is True
        assert OperationLog.objects.filter(module="AI:action").count() == 2

    def test_unknown_user_rejected(self, auth_client, ai_action_settings):
        response = auth_client.post(
            EXECUTE_URL,
            {"action": "user.set_active", "params": {"pk": "no_such_user_x", "is_active": False}},
            format="json",
        )
        assert response.data["code"] == 1001
        assert "no_such_user_x" in str(response.data["detail"])

    def test_requires_business_permission(self, action_client, ai_action_settings, action_user, menu_factory):
        """越权矩阵：无 partialUpdate:UserInfo 不得禁用他人（视图权限链兜底）。"""
        from system.models import UserInfo

        target = UserInfo.objects.create_user(username="victim", password="Test@123456", nickname="受害者")
        grant_perms(action_user.roles.first(), menu_factory, NOTICE_PERMS)
        response = action_client.post(
            EXECUTE_URL, {"action": "user.set_active", "params": {"pk": "victim", "is_active": False}}, format="json"
        )
        assert response.data["code"] == 1001
        target.refresh_from_db()
        assert target.is_active is True

    def test_catalog_lists_action(self, action_user, menu_factory):
        from system.utils.ai_actions import available_actions

        grant_perms(action_user.roles.first(), menu_factory, USER_UPDATE_PERMS)
        keys = [spec.key for spec in available_actions(action_user)]
        assert "user.set_active" in keys


# ---------------------------------------------------------------------------
# 统一目录扩展（助手页改版）：读类动作（IN_QUERY）+ role/menu 参数类型 + 草稿流式端点
# ---------------------------------------------------------------------------


def _parse_sse_frames(response):
    import json as _json

    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def _gather():
            return [chunk async for chunk in content]

        chunks = async_to_sync(_gather)()
    else:
        chunks = content
    frames, buffer = [], b""
    for chunk in chunks:
        buffer += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        while b"\n\n" in buffer:
            raw, buffer = buffer.split(b"\n\n", 1)
            event, data = "message", ""
            for line in raw.decode().splitlines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
            frames.append((event, _json.loads(data) if data else {}))
    return frames


class TestCatalogExtensions:
    """新增声明式动作：user.search（读）/ user.update / role.create / role.grant。"""

    LIST_USER_PERMS = (("list:UserInfo", "api/system/user$", "GET"),)
    UPDATE_USER_PERMS = (("partialUpdate:UserInfo", r"api/system/user/(?P<pk>[^/.]+)$", "PATCH"),)
    CREATE_ROLE_PERMS = (("create:UserRole", "api/system/role$", "POST"),)
    UPDATE_ROLE_PERMS = (("partialUpdate:UserRole", r"api/system/role/(?P<pk>[^/.]+)$", "PATCH"),)

    def test_user_search_read_action(self, ai_action_settings, superuser):
        """读类动作（GET + query 参数）dispatch 列表接口：分页响应按成功处理。"""
        from system.utils.ai_actions import execute_action

        result = execute_action(superuser, "user.search", {"username": "admin"})
        assert result["ok"] is True, result
        assert "admin" in [row["username"] for row in result["data"]["results"]]

    def test_user_search_requires_business_permission(self, ai_action_settings, action_user):
        """权限双门：无 list:UserInfo 的用户不得查询用户（数据权限随调用者收窄）。"""
        from system.utils.ai_actions import execute_action

        result = execute_action(action_user, "user.search", {"username": "admin"})
        assert result["ok"] is False

    def test_user_update_partial_fields(self, ai_action_settings, superuser, normal_user):
        """部分字段更新：只改提供的字段（数据权限随调用者；以超管验证功能链路）。"""
        from system.utils.ai_actions import execute_action

        result = execute_action(superuser, "user.update", {"pk": "zhangsan", "nickname": "新昵称"})
        assert result["ok"] is True, result
        normal_user.refresh_from_db()
        assert normal_user.nickname == "新昵称"

    def test_role_create(self, ai_action_settings, superuser):
        """角色管理动作以超管验证（普通用户受字段级权限裁剪，属业务接口既有约束）。"""
        from system.models import UserRole
        from system.utils.ai_actions import execute_action

        result = execute_action(superuser, "role.create", {"name": "运营组", "code": "ops"})
        assert result["ok"] is True, result
        assert UserRole.objects.filter(name="运营组", code="ops").exists()

    def test_role_grant_menu_subtree(self, ai_action_settings, superuser, menu_factory):
        """菜单名解析为「菜单 + 子树全量权限点」（与授权树勾选父节点同语义）。"""
        from system.models import UserRole
        from system.utils.ai_actions import execute_action

        parent = menu_factory("AI授权测试父菜单", menu_type=Menu.MenuChoices.MENU)
        menu_factory("AI授权测试子权限", path="api/system/user$", method="GET", parent=parent)
        target = UserRole.objects.create(name="目标角色", code="target")
        result = execute_action(superuser, "role.grant", {"pk": "目标角色", "menus": ["AI授权测试父菜单"]})
        assert result["ok"] is True, result
        granted = {str(pk) for pk in target.menu.values_list("pk", flat=True)}
        assert str(parent.pk) in granted and len(granted) == 2


class TestActionInterpretStream:
    """助手页指令执行草稿（SSE）：meta → reasoning* → delta* → done | error。"""

    INTERPRET_URL = "/api/system/ai/assistant/action/interpret/stream"

    def test_gate_returns_json(self, action_client, settings):
        settings.AI_ASSISTANT_ENABLED = True
        settings.AI_ACTION_ENABLED = False
        response = action_client.post(self.INTERPRET_URL, {"message": "查用户"}, format="json")
        assert response.json()["code"] == 1001

    def test_accept_header_negotiation(self, ai_action_settings, action_client, monkeypatch):
        """浏览器 fetch（Accept: text/event-stream）不得 406（流式动作须登记 SSE 渲染器）。"""

        def fake_stream(self, messages, **kwargs):
            yield {"type": "content", "text": '{"action": null, "message": "ok"}'}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = action_client.post(
            self.INTERPRET_URL, {"message": "查一下"}, format="json", HTTP_ACCEPT="text/event-stream"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

    def test_draft_flow_and_persist(self, ai_action_settings, action_client, action_user, monkeypatch, menu_factory):
        grant_perms(action_user.roles.first(), menu_factory, TestCatalogExtensions.LIST_USER_PERMS)

        def fake_stream(self, messages, **kwargs):
            yield {"type": "reasoning", "text": "分析请求"}
            yield {"type": "content", "text": draft_answer("user.search", {"username": "ai_actor"})}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        response = action_client.post(self.INTERPRET_URL, {"message": "查一下 ai_actor"}, format="json")
        frames = _parse_sse_frames(response)
        assert [event for event, __ in frames] == ["meta", "reasoning", "delta", "done"]
        done = frames[-1][1]
        assert done["kind"] == "draft"
        assert done["draft"]["action"] == "user.search"
        assert done["message"]["role"] == "assistant"
        assert done["message"]["reasoning"] == "分析请求"
        rows = list(AiChatMessage.objects.all().order_by("id"))
        assert [row.role for row in rows] == ["user", "assistant"]
        assert rows[1].extra["action_draft"]["action"] == "user.search"

    def test_clarification_message(self, ai_action_settings, action_client, monkeypatch):
        def fake_stream(self, messages, **kwargs):
            yield {
                "type": "content",
                "text": json.dumps({"action": None, "message": "请说明要查谁"}, ensure_ascii=False),
            }

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        frames = _parse_sse_frames(action_client.post(self.INTERPRET_URL, {"message": "查用户"}, format="json"))
        assert frames[-1][0] == "done"
        assert frames[-1][1]["kind"] == "message"
        assert "请说明要查谁" in frames[-1][1]["message"]["content"]

    def test_permission_error_event(self, ai_action_settings, action_client, monkeypatch):
        """草稿目标动作无业务权限：error 事件（白名单 + 权限双门在草稿期生效）。"""

        def fake_stream(self, messages, **kwargs):
            yield {"type": "content", "text": draft_answer("user.search", {"username": "ai_actor"})}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)
        frames = _parse_sse_frames(action_client.post(self.INTERPRET_URL, {"message": "查一下"}, format="json"))
        assert frames[-1][0] == "error"
        assert "permission" in str(frames[-1][1]["detail"]).lower() or "权限" in str(frames[-1][1]["detail"])
