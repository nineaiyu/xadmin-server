# -*- coding: utf-8 -*-
"""通知消息模板可配置集成测试。

口径钉死：
- 覆盖为可选层：未配置时渲染结果与代码默认完全一致（零行为变化）；
- subject / body 可分别覆盖，变量插值走沙箱渲染（未知变量留空，不抛错）；
- 保存时校验模板语法与变量白名单；reset 回到默认；
- HTML 渠道（站内信/邮件）正文在渲染收口统一白名单净化，SMS 纯文本渠道不净化。
"""

import pytest

from notifications.backends import BACKEND
from notifications.models import MessageContent, MessageTemplate, SystemMsgSubscription
from notifications.notifications import Message, SystemMessage, UserMessage
from notifications.template_registry import apply_override, extract_variables, invalidate_overrides, validate_template

pytestmark = pytest.mark.django_db

LIST_URL = "/api/notifications/message-templates"
SAVE_URL = "/api/notifications/message-templates/save"
RESET_URL = "/api/notifications/message-templates/reset"
PREVIEW_URL = "/api/notifications/message-templates/preview"


class _DummyMessage(Message):
    """测试用消息（不注册进全局注册表）：固定 subject/body + 一个业务变量。"""

    message_type_label = "Dummy"
    category = "Test"
    category_label = "Test"

    def get_html_msg(self):
        return {"subject": "默认主题", "message": "<p>默认正文</p>"}

    def get_template_vars(self):
        return {"name": "测试用户"}


class TestOverrideRendering:
    def test_no_override_keeps_default(self):
        message = _DummyMessage()
        assert message.apply_template_override({"subject": "S", "message": "M"}) == {"subject": "S", "message": "M"}

    def test_subject_and_body_override(self):
        MessageTemplate.objects.create(
            message_type="_DummyMessage",
            subject_template="[公司] {{ subject }}",
            body_template="<div>{{ message }}—— {{ name }}</div>",
        )
        invalidate_overrides()
        result = _DummyMessage().apply_template_override({"subject": "默认主题", "message": "正文"})
        assert result["subject"] == "[公司] 默认主题"
        assert "测试用户" in result["message"]
        assert "正文" in result["message"]

    def test_body_only_override(self):
        MessageTemplate.objects.create(message_type="_DummyMessage", body_template="覆盖正文")
        invalidate_overrides()
        result = _DummyMessage().apply_template_override({"subject": "S", "message": "M"})
        assert result["subject"] == "S"
        assert result["message"] == "覆盖正文"

    def test_apply_override_direct(self):
        MessageTemplate.objects.create(message_type="X", body_template="{{ custom }}")
        invalidate_overrides()
        result = apply_override("X", {"subject": "s", "message": "m"}, extra={"custom": "C"})
        assert result["message"] == "C"

    def test_unknown_message_type_zero_change(self):
        assert apply_override("NotConfigured", {"subject": "s", "message": "m"}) == {
            "subject": "s",
            "message": "m",
        }

    def test_inactive_override_ignored(self):
        MessageTemplate.objects.create(message_type="_DummyMessage", body_template="X", is_active=False)
        invalidate_overrides()
        result = _DummyMessage().apply_template_override({"subject": "S", "message": "M"})
        assert result["message"] == "M"


class TestTemplateValidation:
    def test_extract_variables(self):
        assert extract_variables("{{ a }} {{ b.c }} {%% if a %%}") == {"a", "b.c"}

    def test_validate_syntax_error(self):
        assert validate_template("{% if %}", ["subject"])
        assert validate_template("{% unknown_tag %}", ["subject"])

    def test_validate_unknown_variable(self):
        error = validate_template("{{ unknown_var }}", ["subject", "message"])
        assert "unknown_var" in error

    def test_validate_pass(self):
        assert validate_template("{{ subject }} {{ message }}", ["subject", "message"]) == ""


class TestTemplateApi:
    def test_registry_list(self, auth_client):
        resp = auth_client.get(LIST_URL)
        assert resp.data["code"] == 1000, resp.data
        items = resp.data["data"]
        assert any(item["message_type"] == "ApprovalFlowMessage" for item in items)
        approval = next(item for item in items if item["message_type"] == "ApprovalFlowMessage")
        assert "title" in approval["variables"]
        assert approval["has_override"] is False

    def test_save_preview_reset_flow(self, auth_client):
        resp = auth_client.post(
            SAVE_URL,
            {
                "message_type": "ApprovalFlowMessage",
                "subject_template": "[审批] {{ subject }}",
                "body_template": "<p>{{ title }}</p>{{ message }}",
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert MessageTemplate.objects.filter(message_type="ApprovalFlowMessage").exists()
        invalidate_overrides()

        resp = auth_client.get(LIST_URL)
        approval = next(item for item in resp.data["data"] if item["message_type"] == "ApprovalFlowMessage")
        assert approval["has_override"] is True

        resp = auth_client.post(PREVIEW_URL, {"message_type": "ApprovalFlowMessage"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert "[审批]" in resp.data["data"]["subject"]
        assert "title" in resp.data["data"]["variables"]

        resp = auth_client.post(RESET_URL, {"message_type": "ApprovalFlowMessage"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not MessageTemplate.objects.filter(message_type="ApprovalFlowMessage").exists()

    def test_save_rejects_unknown_variable(self, auth_client):
        resp = auth_client.post(
            SAVE_URL,
            {"message_type": "ApprovalFlowMessage", "body_template": "{{ not_a_var }}"},
            format="json",
        )
        assert resp.data["code"] == 1001, resp.data
        assert "not_a_var" in str(resp.data["detail"])

    def test_save_rejects_unknown_message_type(self, auth_client):
        resp = auth_client.post(SAVE_URL, {"message_type": "NopeMessage"}, format="json")
        assert resp.data["code"] == 1001

    def test_publish_pipeline_uses_override(self, superuser):
        """发布链路（get_backend_msg_mapper）套用覆盖：站内信正文被替换"""
        MessageTemplate.objects.create(
            message_type="_Msg",
            subject_template="[定制] {{ subject }}",
        )
        invalidate_overrides()

        class _Msg(UserMessage):
            message_type_label = "x"
            category = "x"
            category_label = "x"

            def get_html_msg(self):
                return {"subject": "导出完成", "message": "正文"}

        mapper = _Msg(superuser).get_backend_msg_mapper(["site_msg"])
        assert mapper
        assert next(iter(mapper.values()))["subject"] == "[定制] 导出完成"


class TestTemplateSanitization:
    """F5①：HTML 渠道模板渲染净化补链（存量缺口——覆盖渲染路径此前不经净化直落库）。"""

    PAYLOAD = '<script>alert(1)</script><img src=x onerror="alert(1)">'

    def _override_body(self, body_template):
        MessageTemplate.objects.create(message_type="_DummyMessage", body_template=body_template)
        invalidate_overrides()

    def test_site_msg_mapper_sanitizes_override_body(self, superuser):
        """站内信渠道：模板正文携带 payload → 渲染收口剥离，{{var}} 插值保留。"""
        self._override_body("<p>{{ message }}——{{ name }}</p>" + self.PAYLOAD)
        mapper = _DummyMessage().get_backend_msg_mapper(["site_msg"])
        body = mapper[BACKEND.SITE_MSG]["message"]
        assert "正文" in body and "测试用户" in body, "变量插值照常生效"
        assert "<script" not in body
        assert "onerror" not in body
        assert "<p>" in body, "白名单排版标签保留"

    def test_email_mapper_sanitizes_body(self, superuser, settings):
        """邮件渠道（html_message 投递）同样净化，签名（白名单内标签）不受影响。"""
        settings.EMAIL_ENABLED = True
        self._override_body("<p>{{ message }}</p>" + self.PAYLOAD)
        mapper = _DummyMessage().get_backend_msg_mapper(["email"])
        assert "<script" not in mapper[BACKEND.EMAIL]["message"]
        assert "onerror" not in mapper[BACKEND.EMAIL]["message"]
        assert "Xadmin Server" in mapper[BACKEND.EMAIL]["message"]

    def test_variable_value_payload_sanitized(self, superuser):
        """变量值（用户可控业务数据）携带 payload → 渠道渲染后同样被剥离。"""
        payload = self.PAYLOAD

        class _PayloadVars(_DummyMessage):
            def get_template_vars(self):
                return {"name": payload}

        self._override_body("<div>{{ name }}</div>")
        mapper = _PayloadVars().get_backend_msg_mapper(["site_msg"])
        body = mapper[BACKEND.SITE_MSG]["message"]
        assert "<script" not in body
        assert "onerror" not in body

    def test_publish_lands_sanitized_message(self, superuser):
        """落库断言：site_msg 渠道 base_notify 落库的 MessageContent.message 已剥离 payload。"""
        MessageTemplate.objects.create(
            message_type="_SanitizedMsg", body_template="<p>{{ message }}</p>" + self.PAYLOAD
        )
        invalidate_overrides()

        class _SanitizedMsg(SystemMessage):
            category = "x"
            category_label = "x"
            message_type_label = "x"

            def get_html_msg(self):
                return {"subject": "净化断言", "message": "<p>正文</p>"}

        sub = SystemMsgSubscription.objects.create(message_type="_SanitizedMsg", receive_backends=["site_msg"])
        sub.users.add(superuser)
        _SanitizedMsg().publish()
        content = MessageContent.objects.get(title="净化断言")
        assert "正文" in content.message
        assert "<script" not in content.message
        assert "onerror" not in content.message

    def test_sms_channel_body_not_html_sanitized(self, superuser, monkeypatch, settings):
        """SMS 纯文本渠道不做 HTML 净化（正文经 html2text 剥标签，净化反而会转义）。

        以标记函数作对照：site_msg（站内信恒发）走了净化，SMS 渠道输出未经触碰。
        """
        settings.SMS_ENABLED = True
        # SMS 渠道可用还需通知签名/模板配置（is_enable 双重门槛）
        settings.SMS_NOTIFY_SIGN_NAME = "签名"
        settings.SMS_NOTIFY_TEMPLATE_CODE = "SMS_TEST"

        def spy(value):
            return "SANITIZED-MARKER"

        monkeypatch.setattr("notifications.notifications.sanitize_rich_text", spy)

        class _SmsMsg(UserMessage):
            message_type_label = "x"
            category = "x"
            category_label = "x"

            def get_html_msg(self):
                return {"subject": "s", "message": "<p>1 &lt; 2</p>"}

        mapper = _SmsMsg(superuser).get_backend_msg_mapper(["sms"])
        assert set(mapper) == {BACKEND.SITE_MSG, BACKEND.SMS}
        assert "SANITIZED-MARKER" not in mapper[BACKEND.SMS]["message"], "SMS 正文未被 HTML 净化"
        assert "SANITIZED-MARKER" in mapper[BACKEND.SITE_MSG]["message"], "对照：HTML 渠道走了净化"

    def test_preview_output_sanitized(self, auth_client):
        """preview 端点输出净化：草稿模板携带 payload → 响应正文已剥离，插值保留。"""
        resp = auth_client.post(
            PREVIEW_URL,
            {"message_type": "ApprovalFlowMessage", "body_template": "<p>{{ title }}</p>" + self.PAYLOAD},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        body = resp.data["data"]["message"]
        assert "<script" not in body
        assert "onerror" not in body
        assert "<p>" in body
