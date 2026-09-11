# -*- coding: utf-8 -*-
"""通知发布管线单测：渠道分发 / 订阅过滤 / 渲染注册表 / 异常吞噬。

覆盖 notifications/notifications.py 中未测试的分支：
- 后端渲染方法注册表回退与禁用后端跳过；
- markdown / text / sms / dingtalk 等渲染缓存属性；
- send_msg 对渠道异常的吞噬（NotImplementedError 跳过 / 其他异常打印堆栈）；
- SystemMessage / UserMessage 的 publish 落库路径（site_msg 渠道真实写库）。
"""

from unittest import mock

import pytest
from django.core import mail

from notifications.backends import BACKEND, client_name_mapper
from notifications.models import MessageContent, SystemMsgSubscription, UserMsgSubscription
from notifications.notifications import (
    Message,
    SystemMessage,
    UserMessage,
    publish_task,
)
from system.notifications import DifferentCityLoginMessage, ResetPasswordSuccessMsg, SensitiveOperationMessage

pytestmark = pytest.mark.django_db


class HtmlMockMessage(Message):
    """携带固定 HTML 内容的测试消息：驱动各渲染分支。"""

    text_msg_ignore_links = True

    def get_common_msg(self) -> dict:
        return {"subject": "mock-subject", "message": "<b>加粗</b> <a href='http://example.com/x'>链接文字</a>"}


class MockSystemNotice(SystemMessage):
    """未注册的系统消息：publish 只要求订阅行存在，不要求进注册表。"""

    category = "Mock"
    category_label = "Mock"
    message_type_label = "Mock notice"

    def get_common_msg(self) -> dict:
        return {"subject": "mock-subject", "message": "<b>mock</b>"}


@pytest.fixture
def system_subscription(superuser):
    """为 MockSystemNotice 建订阅：仅站内信渠道，接收人含超管。"""
    sub = SystemMsgSubscription.objects.create(message_type="MockSystemNotice", receive_backends=["site_msg"])
    sub.users.add(superuser)
    return sub


class TestBackendMsgMapper:
    def test_disabled_backend_is_skipped(self, superuser, settings):
        """被禁用的渠道不进入发送映射（site_msg 恒在）。"""
        settings.EMAIL_ENABLED = False
        mapper = HtmlMockMessage().get_backend_msg_mapper(["email", "site_msg"])
        assert set(mapper) == {BACKEND.SITE_MSG}

    def test_enabled_email_backend_uses_registered_renderer(self, superuser, settings):
        settings.EMAIL_ENABLED = True
        mapper = HtmlMockMessage().get_backend_msg_mapper(["email"])
        assert set(mapper) == {BACKEND.EMAIL, BACKEND.SITE_MSG}
        # 邮件渲染走注册表指向的 get_email_msg（含签名）
        assert "Xadmin Server" in mapper[BACKEND.EMAIL]["message"]


class TestMessageRenderers:
    def test_common_msg_and_html_defaults(self):
        """基类 get_html_msg 退回 get_common_msg 的空文案。"""
        base = Message()
        assert base.get_common_msg() == {"subject": "", "message": ""}
        assert base.get_html_msg() == {"subject": "", "message": ""}

    def test_text_markdown_and_cached_properties(self):
        msg = HtmlMockMessage()
        # 文本渲染：链接目标被剥离，仅保留链接文字
        text = msg.text_msg
        assert "链接文字" in text["message"]
        assert "http://example.com/x" not in text["message"]
        # markdown 渲染
        markdown = msg.markdown_msg
        assert "链接文字" in markdown["message"]
        # 各 cached_property 与直接方法一致
        assert msg.common_msg["subject"] == "mock-subject"
        assert msg.html_msg["message"].startswith("<b>")

    def test_text_msg_keeps_links_when_not_ignored(self):
        class KeepLinksMessage(HtmlMockMessage):
            text_msg_ignore_links = False

        text = KeepLinksMessage().text_msg
        assert "http://example.com/x" in text["message"]

    def test_html_to_markdown_pure_function(self):
        result = Message.html_to_markdown({"subject": "s", "message": "<b>x</b>"})
        assert "x" in result["message"]

    def test_email_msg_appends_signature(self):
        msg = HtmlMockMessage()
        email_msg = msg.get_email_msg()
        assert "Xadmin Server" in email_msg["message"]

    def test_site_msg_renderer_returns_html(self):
        msg = HtmlMockMessage()
        assert msg.get_site_msg_msg() == msg.get_html_msg()

    def test_sms_msg_appends_signature(self):
        msg = HtmlMockMessage()
        sms_msg = msg.get_sms_msg()
        assert "Xadmin Server" in sms_msg["message"]

    def test_dingtalk_msg_appends_time_suffix(self):
        """钉钉渠道按天去重：消息尾部追加时间序号。"""
        msg = HtmlMockMessage()
        ding = msg.get_dingtalk_msg()
        assert ding["subject"] == "mock-subject"
        assert ding["message"].startswith(msg.markdown_msg["message"])


class TestSendMsgErrorPaths:
    def test_not_implemented_backend_is_skipped(self, normal_user):
        """渠道客户端未实现 send_msg 时静默跳过，不影响其他渠道。"""

        class NotSupportedClient:
            @staticmethod
            def send_msg(*args, **kwargs):
                raise NotImplementedError

        with mock.patch.dict(client_name_mapper, {BACKEND.SITE_MSG: NotSupportedClient}):
            Message.send_msg([normal_user.pk], {BACKEND.SITE_MSG: {"subject": "s", "message": "m"}})
        assert MessageContent.objects.count() == 0

    def test_unexpected_error_is_swallowed_with_traceback(self, normal_user, capsys):
        """渠道其他异常只打印堆栈，不向外抛（发送失败不炸调用方）。"""

        class BrokenClient:
            @staticmethod
            def send_msg(*args, **kwargs):
                raise ValueError("boom")

        with mock.patch.dict(client_name_mapper, {BACKEND.SITE_MSG: BrokenClient}):
            Message.send_msg([normal_user.pk], {BACKEND.SITE_MSG: {"subject": "s", "message": "m"}})
        assert "boom" in capsys.readouterr().err


class TestSendTestMsg:
    def test_no_test_msg_is_noop(self):
        """gen_test_msg 返回 None 的消息类型不发任何内容。"""
        before = MessageContent.objects.count()
        ResetPasswordSuccessMsg.send_test_msg()
        assert MessageContent.objects.count() == before

    def test_test_msg_send_path_runs(self, normal_user):
        """gen_test_msg 有内容时真实送达：站内信落库到样例用户。

        回归点：此前 send_test_msg 把空 list 当 backends_msg_mapper 传给
        Message.send_msg（期望 dict），迭代 `.items()` 抛 AttributeError 被吞，
        测试消息从未送达；现走 get_backend_msg_mapper 渠道映射，测试消息应真实落库。
        """
        before = MessageContent.objects.count()
        DifferentCityLoginMessage.send_test_msg()
        assert MessageContent.objects.count() == before + 1
        content = MessageContent.objects.latest("created_time")
        assert list(content.notice_user.values_list("pk", flat=True)) == [normal_user.pk]


class TestSystemMessagePublish:
    def test_publish_without_users_only_warns(self, system_subscription):
        """订阅无接收人时告警并直接返回，不落任何消息。"""
        system_subscription.users.clear()
        before = MessageContent.objects.count()
        MockSystemNotice().publish()
        assert MessageContent.objects.count() == before

    def test_publish_creates_site_message_for_users(self, system_subscription, superuser):
        """站内信渠道真实落库：系统通知 + 接收人关联。"""
        MockSystemNotice().publish()
        content = MessageContent.objects.get(title="mock-subject")
        assert content.notice_type == MessageContent.NoticeChoices.SYSTEM
        assert list(content.notice_user.values_list("pk", flat=True)) == [superuser.pk]

    def test_publish_async_delegates_to_task(self, system_subscription, superuser):
        with mock.patch.object(publish_task, "delay") as fake_delay:
            MockSystemNotice().publish(is_async=True)
        args, _ = fake_delay.call_args
        assert list(args[0]) == [superuser.pk]

    def test_publish_filters_disabled_backends(self, system_subscription, superuser, settings):
        """订阅里登记了 email 但渠道被禁用时，只发站内信。"""
        settings.EMAIL_ENABLED = False
        system_subscription.receive_backends = ["site_msg", "email"]
        system_subscription.save()
        MockSystemNotice().publish()
        assert MessageContent.objects.count() == 1

    def test_post_insert_to_db_is_noop_hook(self, superuser):
        """基类 post_insert_to_db 为空实现，供 post_migrate 回调覆写。"""
        sub = SystemMsgSubscription(message_type="whatever")
        assert SystemMessage.post_insert_to_db(sub) is None

    def test_base_and_system_gen_test_msg_raise(self):
        with pytest.raises(NotImplementedError):
            Message.gen_test_msg()
        with pytest.raises(NotImplementedError):
            SystemMessage.gen_test_msg()

    def test_sensitive_operation_self_heal_subscription(self, superuser):
        """敏感操作告警：订阅无收件人时自愈补齐活跃超管后发布。"""
        SystemMsgSubscription.objects.get_or_create(message_type="SensitiveOperationMessage")
        msg = SensitiveOperationMessage(
            {"module": "test", "path": "/api/x", "method": "DELETE", "ipaddress": "127.0.0.1"}
        )
        msg.publish()
        content = MessageContent.objects.get(notice_type=MessageContent.NoticeChoices.SYSTEM, title__icontains="DELETE")
        assert list(content.notice_user.values_list("pk", flat=True)) == [superuser.pk]


class TestUserMessagePublish:
    def test_publish_with_subscription_creates_site_message(self, normal_user):
        """有订阅时按订阅渠道发送（site_msg 真实落库）。

        标题是固定文案（"异地登录提醒"），IP/城市在正文模板里——
        断言按正文校验，避免把本地化标题写死进测试。
        """
        UserMsgSubscription.objects.create(
            user=normal_user, message_type="DifferentCityLoginMessage", receive_backends=["site_msg"]
        )
        DifferentCityLoginMessage(normal_user, ip="1.1.1.1", city="测试城市").publish()
        content = MessageContent.objects.latest("created_time")
        assert content.notice_type == MessageContent.NoticeChoices.SYSTEM
        assert list(content.notice_user.values_list("pk", flat=True)) == [normal_user.pk]
        assert "1.1.1.1" in content.message and "测试城市" in content.message

    def test_publish_without_subscription_still_sends_site_msg(self, normal_user):
        """无订阅时 receive_backends 为空，站内信作为兜底渠道仍发送。"""
        before = MessageContent.objects.count()
        DifferentCityLoginMessage(normal_user, ip="2.2.2.2", city="测试城市").publish()
        assert MessageContent.objects.count() == before + 1

    def test_publish_with_email_subscription_delivers_mail(self, normal_user, settings):
        """邮件渠道启用且用户绑定邮箱时，异步邮件真实入 outbox。"""
        settings.EMAIL_ENABLED = True
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        UserMsgSubscription.objects.create(
            user=normal_user, message_type="DifferentCityLoginMessage", receive_backends=["email"]
        )
        DifferentCityLoginMessage(normal_user, ip="3.3.3.3", city="测试城市").publish()
        assert len(mail.outbox) == 1
        assert "zhangsan@example.com" in mail.outbox[0].to

    def test_publish_email_skips_user_without_email(self, normal_user, settings):
        """渠道可达性：未绑定邮箱的用户被邮件渠道跳过（不产生邮件、不报错）。"""
        settings.EMAIL_ENABLED = True
        normal_user.email = ""
        normal_user.save(update_fields=["email"])
        UserMsgSubscription.objects.create(
            user=normal_user, message_type="DifferentCityLoginMessage", receive_backends=["email"]
        )
        DifferentCityLoginMessage(normal_user, ip="5.5.5.5", city="测试城市").publish()
        assert len(mail.outbox) == 0

    def test_publish_async_delegates_to_task(self, normal_user):
        with mock.patch.object(publish_task, "delay") as fake_delay:
            DifferentCityLoginMessage(normal_user, ip="4.4.4.4", city="测试城市").publish(is_async=True)
        args, _ = fake_delay.call_args
        assert list(args[0]) == [normal_user.pk]


class TestMessageClassContracts:
    def test_base_publish_and_gen_test_msg_raise(self):
        with pytest.raises(NotImplementedError):
            Message().publish()
        with pytest.raises(NotImplementedError):
            Message.gen_test_msg()

    def test_user_message_gen_test_msg_raise_and_test_user(self, normal_user):
        with pytest.raises(NotImplementedError):
            UserMessage.gen_test_msg()
        # 测试用户取全量用户的第一条
        assert UserMessage.get_test_user() is not None

    def test_reset_password_gen_test_msg_returns_none(self):
        assert ResetPasswordSuccessMsg.gen_test_msg() is None

    def test_get_all_sub_messages_contains_builtin_types(self):
        subclasses = set(Message.get_all_sub_messages())
        assert DifferentCityLoginMessage in subclasses
        assert SensitiveOperationMessage in subclasses

    def test_test_all_messages_tolerates_not_implemented(self):
        """test_all_messages 只跳过 NotImplementedError 的消息类型。"""
        with mock.patch.object(Message, "send_test_msg") as fake_send:
            fake_send.side_effect = NotImplementedError
            Message.test_all_messages()
        assert fake_send.call_count >= 1


class TestAsyncPublishTask:
    def test_publish_task_invokes_send_msg(self, normal_user):
        """publish_task 即 Message.send_msg 的任务壳（eager 模式直接同步执行）。"""
        with mock.patch.object(Message, "send_msg") as fake_send:
            publish_task([normal_user.pk], {BACKEND.SITE_MSG: {"subject": "s", "message": "m"}})
        fake_send.assert_called_once_with([normal_user.pk], {BACKEND.SITE_MSG: {"subject": "s", "message": "m"}})
