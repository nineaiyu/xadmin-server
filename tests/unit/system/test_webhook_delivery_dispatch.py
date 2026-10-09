# -*- coding: utf-8 -*-
"""Webhook 投递派发回归：代际号竞态防护与派发参数契约。

- 旧代际任务到期后静默失效（不投递、不改状态）；
- 过期结果不落库、不续派（条件更新 0 行即放弃）；
- 派发任务每次携带全新 task_id 与递增代际号（不再复用投递主键）；
- 正常成功 / 失败重试 / 耗尽路径行为不变，幂等键 X-Xadmin-Delivery 不变。
"""

import pytest
from django.utils import timezone

from task.models.webhook import WebhookDelivery, WebhookSubscription
from task.utils.webhook import MAX_ATTEMPTS, emit_webhook_event, encrypt_secret
from task.webhook_tasks import deliver_webhook, dispatch_deliver_webhook

pytestmark = pytest.mark.django_db

DELIVERY_URL = "/api/task/webhooks/deliveries"


def make_subscription(event="user.login_succeeded", **kw):
    defaults = {
        "name": "订阅-" + event,
        "url": "http://127.0.0.1:9/hook",
        "secret": encrypt_secret("whs-gen"),
        "events": [event],
    }
    defaults.update(kw)
    return WebhookSubscription.objects.create(**defaults)


def make_delivery(sub, event="user.login_succeeded", **kw):
    defaults = {
        "subscription": sub,
        "event": event,
        "payload": {
            "event": event,
            "schema_version": 1,
            "occurred_at": timezone.now().isoformat(),
            "data": {},
        },
    }
    defaults.update(kw)
    return WebhookDelivery.objects.create(**defaults)


@pytest.fixture
def post_stub(monkeypatch):
    """离线桩替换 _post：记录调用（头/幂等键），响应码可动态调整。"""
    calls = []
    result = {"code": 200, "text": "ok"}

    def fake_post(client, url, body, headers):
        calls.append({"delivery": headers.get("X-Xadmin-Delivery"), "event": headers.get("X-Xadmin-Event")})
        return result["code"], result["text"]

    monkeypatch.setattr("task.webhook_tasks._post", fake_post)
    return calls, result


@pytest.fixture
def dispatch_recorder(monkeypatch):
    """替换任务对象为假桩：捕获派发参数，任务体不执行。"""

    class _FakeTask:
        def __init__(self):
            self.calls = []

        def apply(self, kwargs=None, task_id=None, countdown=None):
            self.calls.append({"kwargs": kwargs, "task_id": task_id, "countdown": countdown})

        apply_async = apply

    fake = _FakeTask()
    monkeypatch.setattr("task.webhook_tasks.deliver_webhook", fake)
    return fake


class TestStaleGeneration:
    def test_stale_task_not_delivered(self, post_stub):
        """旧代际任务到期触发：代际不匹配 → 不投递、不改任何状态。"""
        calls, _ = post_stub
        sub = make_subscription()
        delivery = make_delivery(sub)  # 从未派发，generation=0
        deliver_webhook.apply(kwargs={"delivery_id": str(delivery.pk), "generation": 1})
        delivery.refresh_from_db()
        assert calls == []
        assert delivery.status == "pending"
        assert delivery.attempt == 0
        assert delivery.generation == 0

    def test_task_without_generation_still_delivers(self, post_stub):
        """滚动升级兼容：未携带代际号的历史消息按有效处理。"""
        calls, _ = post_stub
        sub = make_subscription()
        delivery = make_delivery(sub)
        deliver_webhook.apply(kwargs={"delivery_id": str(delivery.pk)})
        delivery.refresh_from_db()
        assert len(calls) == 1
        assert delivery.status == "success"
        assert delivery.attempt == 1


class TestSupersededResult:
    @staticmethod
    def _bump_generation_mid_flight(delivery_pk, code, text):
        """构造在途并发重派：投递发出后、结果落库前代际被推进并重置。"""

        def fake_post(client, url, body, headers):
            WebhookDelivery.objects.filter(pk=delivery_pk).update(generation=9, status="pending", attempt=0)
            return code, text

        return fake_post

    def test_failure_result_dropped_and_no_redispatch(self, post_stub, monkeypatch):
        """失败结果过期：不覆盖重派后的状态，也不再续派重试。"""
        _, result = post_stub
        result["code"], result["text"] = 500, "boom"
        sub = make_subscription(event="user.login_failed")
        delivery = make_delivery(sub, event="user.login_failed")
        dispatched = []
        monkeypatch.setattr("task.webhook_tasks.dispatch_deliver_webhook", lambda *a, **kw: dispatched.append((a, kw)))
        monkeypatch.setattr("task.webhook_tasks._post", self._bump_generation_mid_flight(delivery.pk, 500, "boom"))
        deliver_webhook.apply(kwargs={"delivery_id": str(delivery.pk), "generation": 0})
        delivery.refresh_from_db()
        assert delivery.status == "pending"  # 重派方状态未被过期结果覆盖
        assert delivery.attempt == 0
        assert delivery.response_code is None
        assert delivery.response_body == ""
        assert delivery.next_retry_at is None
        assert dispatched == []  # 过期任务不续派

    def test_success_result_dropped(self, post_stub, monkeypatch):
        """成功结果过期：不得把重派后的投递覆盖成 success。"""
        _, result = post_stub
        result["code"], result["text"] = 200, "ok"
        sub = make_subscription()
        delivery = make_delivery(sub)
        monkeypatch.setattr("task.webhook_tasks._post", self._bump_generation_mid_flight(delivery.pk, 200, "ok"))
        deliver_webhook.apply(kwargs={"delivery_id": str(delivery.pk), "generation": 0})
        delivery.refresh_from_db()
        assert delivery.status == "pending"
        assert delivery.attempt == 0
        assert delivery.response_code is None


class TestDispatchContract:
    def test_fresh_task_id_and_generation_per_dispatch(self, dispatch_recorder):
        """每次派发 task_id 全新生成（不复用投递主键）、代际号递增。"""
        sub = make_subscription()
        delivery = make_delivery(sub)
        dispatch_deliver_webhook(str(delivery.pk))
        dispatch_deliver_webhook(str(delivery.pk))
        first, second = dispatch_recorder.calls
        assert first["task_id"] != str(delivery.pk)
        assert second["task_id"] != str(delivery.pk)
        assert first["task_id"] != second["task_id"]
        assert first["kwargs"] == {"delivery_id": str(delivery.pk), "generation": 1}
        assert second["kwargs"] == {"delivery_id": str(delivery.pk), "generation": 2}

    def test_async_dispatch_defers_to_commit_and_keeps_countdown(
        self, dispatch_recorder, settings, django_capture_on_commit_callbacks
    ):
        """非 eager：事务提交后才派发（worker 读到已提交代际号），countdown 透传。"""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        sub = make_subscription()
        delivery = make_delivery(sub)
        with django_capture_on_commit_callbacks(execute=True):
            dispatch_deliver_webhook(str(delivery.pk), countdown=60)
        assert len(dispatch_recorder.calls) == 1
        call = dispatch_recorder.calls[0]
        assert call["task_id"] != str(delivery.pk)
        assert call["kwargs"] == {"delivery_id": str(delivery.pk), "generation": 1}
        assert call["countdown"] == 60

    def test_emit_uses_dispatcher(self, dispatch_recorder):
        """emit 首投也走统一调度助手（带代际号 + 全新 task_id）。"""
        sub = make_subscription(event="webhook.ping")
        assert emit_webhook_event("webhook.ping", {"subscription": sub.name}) == 1
        call = dispatch_recorder.calls[0]
        delivery = WebhookDelivery.objects.get(subscription=sub)
        assert call["task_id"] != str(delivery.pk)
        assert call["kwargs"] == {"delivery_id": str(delivery.pk), "generation": 1}

    def test_dispatch_missing_delivery_is_silent(self):
        dispatch_deliver_webhook("00000000-0000-0000-0000-000000000000")  # 记录告警且不抛异常


class TestNormalPaths:
    def test_success_path_unchanged(self, post_stub):
        """正常成功：状态/attempt/响应字段与幂等键行为不变，代际停在派发值。"""
        calls, result = post_stub
        result["code"], result["text"] = 200, "ok"
        sub = make_subscription()
        delivery = make_delivery(sub)
        dispatch_deliver_webhook(str(delivery.pk))
        delivery.refresh_from_db()
        assert delivery.status == "success"
        assert delivery.attempt == 1
        assert delivery.response_code == 200
        assert delivery.response_body == "ok"
        assert delivery.duration is not None
        assert delivery.next_retry_at is None
        assert delivery.generation == 1  # 成功后无续派，代际不再推进
        assert calls[0]["delivery"] == str(delivery.pk)  # 幂等键恒为主键

    def test_failure_backoff_until_exhausted_unchanged(self, post_stub):
        """失败链路：逐次退避重试至耗尽，attempt/状态/告警面行为不变。"""
        calls, result = post_stub
        result["code"], result["text"] = 500, "boom"
        sub = make_subscription(event="user.login_failed")
        delivery = make_delivery(sub, event="user.login_failed")
        dispatch_deliver_webhook(str(delivery.pk))
        delivery.refresh_from_db()
        assert delivery.status == "exhausted"
        assert delivery.attempt == MAX_ATTEMPTS
        assert delivery.next_retry_at is None
        assert delivery.generation == MAX_ATTEMPTS
        assert len(calls) == MAX_ATTEMPTS
        assert all(call["delivery"] == str(delivery.pk) for call in calls)
        sub.refresh_from_db()
        assert "user.login_failed" in sub.last_failure


class TestRetryAction:
    def test_retry_dispatches_fresh_task_id(self, superuser, auth_client, dispatch_recorder):
        """人工 retry：同事务重置 + 代际递增，派发 task_id 不再是投递主键。"""
        sub = make_subscription(event="user.login_failed")
        delivery = make_delivery(sub, event="user.login_failed", status="exhausted", attempt=5)
        response = auth_client.post(f"{DELIVERY_URL}/{delivery.pk}/retry", {}, format="json")
        assert response.status_code == 200, response.data
        assert len(dispatch_recorder.calls) == 1
        call = dispatch_recorder.calls[0]
        assert call["task_id"] != str(delivery.pk)
        assert call["kwargs"] == {"delivery_id": str(delivery.pk), "generation": 1}
        delivery.refresh_from_db()
        assert delivery.status == "pending"
        assert delivery.attempt == 0
        assert delivery.next_retry_at is None
        assert delivery.generation == 1

    def test_retry_rejected_for_success(self, superuser, auth_client, dispatch_recorder):
        sub = make_subscription()
        delivery = make_delivery(sub, status="success")
        response = auth_client.post(f"{DELIVERY_URL}/{delivery.pk}/retry", {}, format="json")
        assert response.status_code == 200
        assert response.data.get("code") == 1001
        assert dispatch_recorder.calls == []
