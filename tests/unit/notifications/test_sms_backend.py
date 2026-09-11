# -*- coding: utf-8 -*-
"""notifications 通知后端单元测试。"""

from unittest import mock

import pytest

from notifications.backends.sms import SMS

pytestmark = pytest.mark.django_db


class TestSmsBackend:
    def test_client_is_sdk_endpoint_not_self(self, settings):
        """回归测试：__init__ 曾因类名遮蔽 import 而递归实例化自身，
        导致 send_msg 调用 self.client.send_sms 时报 AttributeError。"""
        settings.SMS_BACKEND = "alibaba"
        backend = SMS()
        assert hasattr(backend.client, "send_sms")
        assert backend.client.__class__ is not SMS


class TestSmsNotifyContract:
    """短信通知渠道与消息映射契约（send_msg 接收渲染后的 subject/message）。"""

    def _enable(self, settings, sign_name="签名", template_code="SMS_123"):
        settings.SMS_ENABLED = True
        settings.SMS_NOTIFY_SIGN_NAME = sign_name
        settings.SMS_NOTIFY_TEMPLATE_CODE = template_code
        settings.SMS_NOTIFY_TEMPLATE_PARAM_KEY = "content"

    def test_is_enable_requires_global_switch(self, settings):
        self._enable(settings)
        settings.SMS_ENABLED = False
        assert SMS.is_enable() is False

    def test_is_enable_requires_template_config(self, settings):
        """开关开启但签名/模板未配置时渠道降级为不可用（模板短信无法发自由文本）。"""
        settings.SMS_ENABLED = True
        settings.SMS_NOTIFY_SIGN_NAME = ""
        settings.SMS_NOTIFY_TEMPLATE_CODE = ""
        assert SMS.is_enable() is False

    def test_is_enable_ok_when_configured(self, settings):
        self._enable(settings)
        assert SMS.is_enable() is True

    def test_send_msg_delivers_via_notify_template(self, settings, normal_user):
        self._enable(settings)
        normal_user.phone = "13800000000"
        normal_user.save(update_fields=["phone"])
        with mock.patch("notifications.backends.sms.sms_endpoint.SMS") as fake_endpoint:
            SMS().send_msg([normal_user], "测试正文", subject="测试标题")
        args = fake_endpoint.return_value.send_sms.call_args[0]
        assert args[0] == ["13800000000"]
        assert args[1] == "签名"
        assert args[2] == "SMS_123"
        assert args[3] == {"content": "测试正文"}

    def test_send_msg_skips_users_without_phone(self, settings, normal_user):
        """渠道可达性：未绑定手机号的用户被短信渠道跳过，不调用 SDK。"""
        self._enable(settings)
        assert not normal_user.phone
        with mock.patch("notifications.backends.sms.sms_endpoint.SMS") as fake_endpoint:
            SMS().send_msg([normal_user], "正文")
        fake_endpoint.return_value.send_sms.assert_not_called()

    def test_send_msg_noop_when_template_not_configured(self, settings, normal_user):
        self._enable(settings, sign_name="", template_code="")
        with mock.patch("notifications.backends.sms.sms_endpoint.SMS") as fake_endpoint:
            SMS().send_msg([normal_user], "正文")
        fake_endpoint.return_value.send_sms.assert_not_called()
