# -*- coding: utf-8 -*-
"""出站 Webhook 集成测试。

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
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils.translation import gettext as _

from task.models.webhook import WebhookDelivery, WebhookSubscription
from task.services.webhook import (
    EVENT_CATALOG,
    decrypt_secret,
    emit_webhook_event,
    encrypt_secret,
    sign_payload,
    validate_url,
)

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/task/webhooks/subscriptions"
DELIVERY_URL = "/api/task/webhooks/deliveries"


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
            with pytest.raises(ValidationError):
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
        sub.last_failure = "user.login_failed: old exhausted failure"
        sub.save(update_fields=["last_failure", "updated_time"])
        emit_webhook_event("user.login_succeeded", {"username": "alice"})
        delivery = WebhookDelivery.objects.get(subscription=sub)
        assert delivery.status == "success"
        assert delivery.attempt == 1
        sub.refresh_from_db()
        assert sub.last_failure == "", "成功投递必须清空历史 last_failure"

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

    def test_delivery_exposes_payload_and_signature_headers(self, receiver, superuser, auth_client):
        """审计面能看到请求体与签名头（排障自证「发了什么、怎么签的」）。"""
        url, _handler = receiver
        make_subscription(url=url, event="user.login_succeeded")
        emit_webhook_event("user.login_succeeded", {"username": "audit-user"})

        response = auth_client.get(DELIVERY_URL)
        assert response.status_code == 200, response.data
        row = response.data["data"]["results"][0]
        assert row["payload"]["data"]["username"] == "audit-user"
        headers = row["request_headers"]
        assert headers["X-Xadmin-Event"] == "user.login_succeeded"
        assert headers["X-Xadmin-Delivery"]
        assert headers["X-Xadmin-Timestamp"].isdigit()
        assert headers["X-Xadmin-Signature"].startswith("sha256=")

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

    def test_subscription_secret_flag_and_rotation(self, superuser, auth_client):
        sub = make_subscription(url="https://hooks.example.com/x")
        response = auth_client.get(WEBHOOK_URL)
        row = next(item for item in response.data["data"]["results"] if item["pk"] == str(sub.pk))
        assert row["has_secret"] is True
        assert "secret" not in row

        rotated = auth_client.post(f"{WEBHOOK_URL}/{sub.pk}/rotate-secret", {}, format="json")
        assert rotated.status_code == 200, rotated.data
        new_secret = rotated.data["data"]["secret"]
        assert new_secret and len(new_secret) >= 32
        sub.refresh_from_db()
        assert decrypt_secret(sub.secret) == new_secret
        assert decrypt_secret(sub.secret) != "whs-3cret"

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
            "task.models.webhook.WebhookSubscription.objects",
            property(lambda self: (_ for _ in ()).throw(RuntimeError("db down"))),
        )
        assert emit_webhook_event("user.login_succeeded", {}) == 0

    def test_delivery_records_are_read_only(self, auth_client, receiver):
        """审计记录不可删：单删 405、批量删路由不存在；列表/详情/重试保持可用。"""
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_failed", {})
        delivery = WebhookDelivery.objects.get(subscription=sub)

        assert auth_client.get(DELIVERY_URL).status_code == 200
        assert auth_client.get(f"{DELIVERY_URL}/{delivery.pk}").status_code == 200
        assert auth_client.delete(f"{DELIVERY_URL}/{delivery.pk}").status_code == 405
        assert (
            auth_client.post(f"{DELIVERY_URL}/batch-destroy", {"pks": [str(delivery.pk)]}, format="json").status_code
            == 405
        )
        assert WebhookDelivery.objects.filter(pk=delivery.pk).exists()


class TestEventWiring:
    def test_login_success_emits(self, receiver, superuser):
        url, handler = receiver
        make_subscription(url=url, event="user.login_succeeded")
        from identity.services.auth_login import login_success

        request = RequestFactory().post("/api/identity/login/basic", HTTP_USER_AGENT="pytest-agent")
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
        from approval.utils.approval import approve_request, cancel_request, create_approval
        from common.core.config import SysConfig

        SysConfig.set_value("APPROVAL_ENABLED", True)
        # 定向失效配置缓存（delete_pattern 带 worker 前缀，真环境档下不得 flushdb）
        SysConfig.invalid_config_cache()
        factory = RequestFactory()
        request = factory.post("/api/identity/user/1", HTTP_USER_AGENT="pytest-agent")
        request.user = normal_user
        request.data = {}
        approval = create_approval(_FakeView(), request)
        assert WebhookDelivery.objects.filter(event="approval.submitted").exists()
        assert approve_request(approval, superuser)[0] is True
        assert WebhookDelivery.objects.filter(event="approval.approved").exists()
        assert cancel_request(approval, normal_user)[0] is False  # 已终态不可撤回
        assert handler.received[-1]["event"] == "approval.approved"

    def test_flow_events_emit(self, receiver, superuser, normal_user):
        """流程审批引擎事件接线：提交/通过/撤回 → flow.* 投递。"""
        url, handler = receiver
        for event in ("flow.submitted", "flow.approved", "flow.cancelled"):
            make_subscription(url=url, event=event)
        from approval.models import ApprovalFlow, ApprovalFlowNode, ApprovalNodeTask
        from approval.services.approval_flow import approve_task, cancel_instance, create_instance

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

    def test_test_action_only_targets_current_subscription(self, auth_client, receiver):
        """测试按钮只测当前行：未订阅 ping 也能测，其他订阅了 ping 的订阅者收不到。"""
        url, handler = receiver
        target = make_subscription(name="被测订阅", url=url, event="user.login_failed")
        bystander = make_subscription(name="旁观订阅", url=url, event="webhook.ping")
        response = auth_client.post(f"{WEBHOOK_URL}/{target.pk}/test", {}, format="json")
        assert response.status_code == 200, response.data
        assert response.data["code"] == 1000
        deliveries = WebhookDelivery.objects.filter(event="webhook.ping")
        assert list(deliveries.values_list("subscription_id", flat=True)) == [target.pk]
        assert not WebhookDelivery.objects.filter(subscription=bystander).exists()

    def test_test_action_rejects_inactive_subscription(self, auth_client):
        """停用订阅给出可读原因，且不留注定失败的投递记录。"""
        sub = make_subscription(name="停用订阅", is_active=False)
        response = auth_client.post(f"{WEBHOOK_URL}/{sub.pk}/test", {}, format="json")
        assert response.data["code"] == 1001
        assert response.data["detail"] == str(_("Subscription is inactive, enable it before testing"))
        assert not WebhookDelivery.objects.filter(subscription=sub).exists()

    def test_test_action_rejects_undeliverable_url(self, auth_client):
        """存量 http 目标（非环回）写入侧已不再允许：测试时前置校验给出可读原因。"""
        sub = make_subscription(name="明文地址订阅", url="http://hooks.example.com/x")
        response = auth_client.post(f"{WEBHOOK_URL}/{sub.pk}/test", {}, format="json")
        assert response.data["code"] == 1001
        assert str(response.data["detail"]) == str(_("Subscription url is not deliverable: {}")).format(
            _("Webhook url must use https (loopback http is allowed for local testing)")
        )
        assert not WebhookDelivery.objects.filter(subscription=sub).exists()

    def test_test_action_rejects_broken_secret(self, auth_client):
        """secret 密文损坏（无法解密）时签名必然失效：直接提示重设，不发无效请求。"""
        sub = make_subscription(name="坏密钥订阅", secret="not-a-ciphertext")
        response = auth_client.post(f"{WEBHOOK_URL}/{sub.pk}/test", {}, format="json")
        assert response.data["code"] == 1001
        assert response.data["detail"] == str(_("Subscription secret cannot be decrypted, set it again before testing"))
        assert not WebhookDelivery.objects.filter(subscription=sub).exists()

    def test_deliveries_audit_list(self, auth_client, receiver):
        url, handler = receiver
        sub = make_subscription(url=url, event="user.login_failed")
        emit_webhook_event("user.login_failed", {"username": "bob"})
        body = auth_client.get(DELIVERY_URL, {"status": "success"}).json()
        assert body["data"]["total"] == 1
        assert body["data"]["results"][0]["subscription_name"] == sub.name
