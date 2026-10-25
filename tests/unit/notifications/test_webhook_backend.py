# -*- coding: utf-8 -*-
"""Webhook 通知渠道行为守护。

- ``is_enable`` 双条件（开关 + URL）——开关开而 URL 缺自动降级，订阅矩阵不展示半配置渠道；
- ``send_msg`` 载荷结构 / 目标用户截断 / 签名头（与开放平台 sign_payload 同口径）；
- 出站走 ``pinned_request`` 统一出口（私网默认拒绝、白名单放行），失败吞掉不阻断其他渠道。
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from django.test import override_settings

from notifications.backends.webhook import USERS_LIMIT, Webhook, sign_webhook_payload


class UserStub:
    def __init__(self, pk, username, nickname=""):
        self.pk = pk
        self.username = username
        self.nickname = nickname


@pytest.fixture
def deliver():
    """拦截出站：断言 send_msg 经 pinned_request 出站且参数形态正确（函数内
    import 从源模块取，patch 源模块属性）。"""
    with patch("common.utils.outbound.pinned_request") as mock:
        yield mock


class TestIsEnable:
    def test_disabled_by_default(self):
        with override_settings(WEBHOOK_ENABLED=False, WEBHOOK_URL=""):
            assert Webhook.is_enable() is False

    @override_settings(WEBHOOK_ENABLED=True, WEBHOOK_URL="")
    def test_enabled_without_url_degrades(self):
        """开关开而 URL 缺 → 降级不可用（与 IM 渠道「开而凭据缺」同口径）。"""
        assert Webhook.is_enable() is False

    @override_settings(WEBHOOK_ENABLED=True, WEBHOOK_URL="https://ops.example.com/hook")
    def test_enabled_with_url(self):
        assert Webhook.is_enable() is True


class TestSendMsg:
    @override_settings(WEBHOOK_ENABLED=True, WEBHOOK_URL="https://ops.example.com/hook", WEBHOOK_SECRET="")
    def test_payload_shape_and_no_signature_without_secret(self, deliver):
        users = [UserStub(1, "alice", "爱丽丝"), UserStub(2, "bob")]
        Webhook().send_msg(users, "内容", "标题")
        deliver.assert_called_once()
        args, kwargs = deliver.call_args
        assert args[0] == "POST" and args[1] == "https://ops.example.com/hook"
        assert kwargs["allow_private"] is False
        payload = json.loads(kwargs["data"].decode("utf-8"))
        assert payload == {
            "subject": "标题",
            "message": "内容",
            "users": [
                {"pk": "1", "username": "alice", "nickname": "爱丽丝"},
                {"pk": "2", "username": "bob", "nickname": ""},
            ],
        }
        assert kwargs["headers"] == {"Content-Type": "application/json"}

    @override_settings(WEBHOOK_URL="https://ops.example.com/hook", WEBHOOK_SECRET="s3cret")
    def test_signature_headers_with_secret(self, deliver):
        Webhook().send_msg([UserStub(1, "alice")], "内容", "标题")
        headers = deliver.call_args.kwargs["headers"]
        body = deliver.call_args.kwargs["data"]
        signature = headers["X-Webhook-Signature"]
        timestamp = headers["X-Webhook-Timestamp"]
        expected = hmac.new(b"s3cret", f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        assert signature == f"sha256={expected}"

    @override_settings(WEBHOOK_URL="https://ops.example.com/hook")
    def test_recipients_truncated_to_limit(self, deliver):
        users = [UserStub(i, f"user{i}") for i in range(USERS_LIMIT + 5)]
        Webhook().send_msg(users, "广播", "")
        payload = json.loads(deliver.call_args.kwargs["data"].decode("utf-8"))
        assert len(payload["users"]) == USERS_LIMIT

    @override_settings(WEBHOOK_URL="")
    def test_skip_without_url(self, deliver):
        """URL 未配置时跳过且不出站（幂等降级，不抛错）。"""
        assert Webhook().send_msg([UserStub(1, "alice")], "内容") is None
        deliver.assert_not_called()

    @override_settings(WEBHOOK_URL="https://ops.example.com/hook")
    def test_delivery_failure_is_swallowed(self, deliver):
        """出站失败只记日志（渠道尽力而为），不阻断其他渠道投递。"""
        deliver.side_effect = TimeoutError("boom")
        assert Webhook().send_msg([UserStub(1, "alice")], "内容") is None


class TestSignPayloadParity:
    def test_signature_matches_openplatform_scheme(self):
        """签名口径与开放平台事件投递一致：sha256=HMAC(secret, "{ts}." + body)。"""
        signature, timestamp = sign_webhook_payload("s3cret", b"payload", timestamp=1700000000)
        expected = hmac.new(b"s3cret", b"1700000000.payload", hashlib.sha256).hexdigest()
        assert signature == f"sha256={expected}"
        assert timestamp == 1700000000
