# -*- coding: utf-8 -*-
"""出站 Webhook 集成测试（ADR-022）。

覆盖：签名（HMAC 稳定性 + 本地接收端验签）、指数退避重试与耗尽告警、
订阅过滤（未订阅事件不投递）、URL 白名单、事件接线（登录/审批触发投递）、
越权与 secret 不回显、retry 动作。
"""

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.utils.webhook import (
    EVENT_CATALOG,
    decrypt_secret,
    encrypt_secret,
    emit_webhook_event,
    sign_payload,
    validate_url,
)

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/system/webhooks/subscriptions"
DELIVERY_URL = "/api/system/webhooks/deliveries"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------- 单元


class TestSignAndValidate:
    def test_sign_payload_stable(self):
        sig1, ts = sign_payload("secret", b'{"a":1}', timestamp=1700000000)
        sig2, __ = sign_payload("secret", b'{"a":1}', timestamp=1700000000)
        assert sig1 == sig2
        expected = hmac.new(b"secret", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
        assert sig1 == f"sha256={expected}"

    def test_secret_roundtrip(self):
        encrypted = encrypt_secret("s3cret")
        assert "s3cret" not in encrypted
        assert decrypt_secret(encrypted) == "s3cret"

    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://hooks.example.com/x", True),
            ("http://127.0.0.1:9000/hook", True),
            ("http://localhost:9000/hook", True),
            ("http://hooks.example.com/x", False),
            ("ftp://x", False),
        ],
    )
    def test_url_whitelist(self, url, ok):
        if ok:
            assert validate_url(url) == url
        else:
            with pytest.raises(Exception):
                validate_url(url)


# ---------------------------------------------------------------- 本地接收端


class _Receiver(BaseHTTPRequestHandler):
    """进程内接收端：可配置响应码，记录请求供断言（验签/头/幂等键）。"""

    received = []
    respond_status = 200
    lock = threading.Lock()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        with _Receiver.lock:
            _Receiver.received.append(
                {
                    "body": body,
                    "event": self.headers.get("X-Xadmin-Event"),
                    "delivery": self.headers.get("X-Xadmin-Delivery"),
                    "signature": self.headers.get("X-Xadmin-Signature"),
                    "timestamp": self.headers.get("X-Xadmin-Timestamp"),
                }
            )
        self.send_response(_Receiver.respond_status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    _Receiver.received = []
    _Receiver.respond_status = 200
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hook", _Receiver
    server.shutdown()


def make_subscription(**kw):
    defaults = {
        "name": "订阅-" + kw.get("event", "user.login_succeeded").replace(".", "-"),
        "url": "http://127.0.0.1:9/hook",
        "secret": encrypt_secret("whs-3cret"),
        "events": [kw.get("event", "user.login_succeeded")],
    }
    defaults.update({k: v for k, v in kw.items() if k != "event"})
    return WebhookSubscription.objects.create(**defaults)


class TestDelivery:
    def test_success_delivery_and_signature(self, receiver, superuser):
        """成功投递：本地接收端验签（timestamp.body 的 HMAC）+ 头完整。"""
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_succeeded")
        emit_webhook_event("user.login_succeeded", {"username": "alice"})
        delivery = WebhookDelivery.objects.get(subscription=sub)
        assert delivery.status == "success"
        assert delivery.attempt == 1

        received = handler.received[0]
        expected = hmac.new(
            b"whs-3cret",
            f"{received['timestamp']}.".encode() + received["body"],
            hashlib.sha256,
        ).hexdigest()
        assert received["signature"] == f"sha256={expected}"
        assert received["event"] == "user.login_succeeded"
        payload = json.loads(received["body"])
        assert payload["event"] == "user.login_succeeded"
        assert payload["data"]["username"] == "alice"
        assert payload["occurred_at"]

    def test_unsubscribed_event_not_delivered(self, receiver):
        url, handler = receiver
        make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_succeeded", {})
        assert handler.received == []
        assert WebhookDelivery.objects.count() == 0

    def test_retry_backoff_and_exhaustion_with_alert(self, receiver, superuser):
        """非 2xx → 指数退避重试（EAGER 下逐次触发）→ 耗尽 + 超管告警。"""
        url, handler = receiver
        _Receiver.respond_status = 500
        sub = make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_failed", {"username": "bob"})

        delivery = WebhookDelivery.objects.get(subscription=sub)
        assert delivery.status == "exhausted"
        assert delivery.attempt == 5  # MAX_ATTEMPTS
        assert len(handler.received) == 5
        sub.refresh_from_db()
        assert "user.login_failed" in sub.last_failure
        # 耗尽告警站内信（SystemMessage → MessageContent）
        from notifications.models.message import MessageContent

        assert (
            MessageContent.objects.filter(title__icontains="Webhook").exists()
            or MessageContent.objects.filter(title__contains="投递已耗尽").exists()
        )
        _Receiver.respond_status = 200

    def test_retry_action_resets(self, receiver, superuser, auth_client):
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_failed", {})
        delivery = WebhookDelivery.objects.get(subscription=sub)
        delivery.status = "exhausted"
        delivery.attempt = 5
        delivery.save()

        response = auth_client.post(f"{DELIVERY_URL}/{delivery.pk}/retry", {}, format="json")
        assert response.status_code == 200, response.data
        delivery.refresh_from_db()
        assert delivery.status == "success"  # 接收端恢复 200

    def test_emit_never_raises(self, monkeypatch):
        """发射口吞异常：投递链路故障不影响宿主动作。"""
        monkeypatch.setattr(
            "system.models.webhook.WebhookSubscription.objects",
            property(lambda self: (_ for _ in ()).throw(RuntimeError("db down"))),
        )
        assert emit_webhook_event("user.login_succeeded", {}) == 0


class TestEventWiring:
    def test_login_success_emits(self, receiver, superuser):
        url, handler = receiver
        make_subscription(url=url, event="user.login_succeeded")
        from system.views.auth.login import login_success

        request = RequestFactory().post("/api/system/login/basic", HTTP_USER_AGENT="pytest-agent")
        request.user = superuser
        superuser.date_password_updated = None
        superuser.save()
        login_success(request, superuser)
        assert WebhookDelivery.objects.filter(event="user.login_succeeded").exists()
        assert handler.received[0]["event"] == "user.login_succeeded"

    def test_approval_events_emit(self, receiver, superuser, normal_user, role):
        url, handler = receiver
        for event in ("approval.submitted", "approval.approved", "approval.cancelled"):
            make_subscription(url=url, event=event)
        from common.core.config import SysConfig
        from system.utils.approval import approve_request, cancel_request, create_approval

        SysConfig.set_value("APPROVAL_ENABLED", True)
        cache.clear()
        factory = RequestFactory()
        request = factory.post("/api/system/user/1", HTTP_USER_AGENT="pytest-agent")
        request.user = normal_user
        request.data = {}
        approval = create_approval(_FakeView(), request)
        assert WebhookDelivery.objects.filter(event="approval.submitted").exists()
        assert approve_request(approval, superuser)[0] is True
        assert WebhookDelivery.objects.filter(event="approval.approved").exists()
        assert cancel_request(approval, normal_user)[0] is False  # 已终态不可撤回
        assert handler.received[-1]["event"] == "approval.approved"

    def test_flow_events_emit(self, receiver, superuser, normal_user):
        """流程审批引擎（ADR-012）事件接线：提交/通过/撤回 → flow.* 投递。"""
        url, handler = receiver
        for event in ("flow.submitted", "flow.approved", "flow.cancelled"):
            make_subscription(url=url, event=event)
        from system.models import ApprovalFlow, ApprovalFlowNode, ApprovalNodeTask
        from system.utils.approval_flow import approve_task, cancel_instance, create_instance

        flow = ApprovalFlow.objects.create(name="WH测试流", code="wh_flow_test", form_schema=[])
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="审批",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=superuser.username,
        )

        instance, error = create_instance(flow=flow, applicant=normal_user, title="WH申请", form_data={})
        assert error is None
        assert WebhookDelivery.objects.filter(event="flow.submitted").exists()
        submitted = WebhookDelivery.objects.get(event="flow.submitted")
        # emit_webhook_event 的 payload 包装：{event, occurred_at, data}
        assert submitted.payload["data"]["status"] == "PENDING"
        assert submitted.payload["data"]["title"] == "WH申请"
        assert submitted.payload["data"]["creator"] == normal_user.username

        task = ApprovalNodeTask.objects.get(instance=instance, status=ApprovalNodeTask.Status.PENDING)
        assert approve_task(task.pk, superuser)[0] is True
        assert WebhookDelivery.objects.filter(event="flow.approved").exists()

        instance2, _error = create_instance(flow=flow, applicant=normal_user, title="WH撤回申请", form_data={})
        assert cancel_instance(instance2, normal_user)[0] is True
        assert WebhookDelivery.objects.filter(event="flow.cancelled").exists()
        # 本地接收端未订阅 rejected：本用例仅覆盖 submitted/approved/cancelled 三态
        assert handler.received[-1]["event"] == "flow.cancelled"


class _FakeView:
    """create_approval 需要 view 提供 module/object_pk 信息。"""

    def __init__(self):
        self.queryset = None
        self.request = None
        self.kwargs = {}
        self.action = "create"
        self.detail = False

    def get_queryset(self):
        return None

    def __getattr__(self, item):
        return None


# ---------------------------------------------------------------- API


class TestSubscriptionApi:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(WEBHOOK_URL).status_code == 401

    def test_secret_write_only(self, auth_client):
        payload = {
            "name": "外部系统",
            "url": "https://hooks.example.com/x",
            "secret": "top-secret",
            "events": ["user.login_succeeded"],
        }
        response = auth_client.post(WEBHOOK_URL, payload, format="json")
        assert response.status_code == 200, response.data
        body = auth_client.get(WEBHOOK_URL).json()
        assert "top-secret" not in str(body)
        row = WebhookSubscription.objects.get(name="外部系统")
        assert row.secret != "top-secret"  # 密文落库
        assert decrypt_secret(row.secret) == "top-secret"

    def test_events_catalog(self, auth_client):
        body = auth_client.get(f"{WEBHOOK_URL}/events").json()
        keys = {item["key"] for item in body["data"]}
        assert keys == set(EVENT_CATALOG)

    def test_unknown_event_rejected(self, auth_client):
        response = auth_client.post(
            WEBHOOK_URL,
            {"name": "坏事件", "url": "https://x.example.com", "secret": "s", "events": ["nope"]},
            format="json",
        )
        assert response.status_code == 400

    def test_http_url_rejected(self, auth_client):
        response = auth_client.post(
            WEBHOOK_URL,
            {"name": "明文", "url": "http://hooks.example.com", "secret": "s", "events": ["user.login_succeeded"]},
            format="json",
        )
        assert response.status_code == 400

    def test_test_action_dispatches_ping(self, auth_client, receiver):
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_failed")
        sub.events = ["user.login_failed", "webhook.ping"]
        sub.save()
        response = auth_client.post(f"{WEBHOOK_URL}/{sub.pk}/test", {}, format="json")
        assert response.status_code == 200, response.data
        assert WebhookDelivery.objects.filter(event="webhook.ping", subscription=sub).exists()

    def test_deliveries_audit_list(self, auth_client, receiver):
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_failed", {"username": "bob"})
        body = auth_client.get(DELIVERY_URL, {"status": "success"}).json()
        assert body["data"]["total"] == 1
        assert body["data"]["results"][0]["subscription_name"] == sub.name
