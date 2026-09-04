# -*- coding: utf-8 -*-
"""notifications 通知后端单元测试。"""

from notifications.backends.sms import SMS


class TestSmsBackend:
    def test_client_is_sdk_endpoint_not_self(self, settings):
        """回归测试：__init__ 曾因类名遮蔽 import 而递归实例化自身，
        导致 send_msg 调用 self.client.send_sms 时报 AttributeError。"""
        settings.SMS_BACKEND = "alibaba"
        backend = SMS()
        assert hasattr(backend.client, "send_sms")
        assert backend.client.__class__ is not SMS