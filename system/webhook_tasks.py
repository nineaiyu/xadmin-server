#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 投递任务：HMAC 签名 POST + 指数退避重试 + 耗尽告警。

- task_id == WebhookDelivery.pk（幂等键，接收方按 X-Xadmin-Delivery 去重）；
- 失败 countdown = min(60 × 2^attempt, 3600) 指数退避，上限 5 次尝试；
- 耗尽 → 投递置 exhausted + 站内信告警超管（WebhookFailedMessage）；
- http 客户端可注入（单测离线）；投递链路任何异常不外抛（后台任务语义）。
"""

import json
import time

from celery import shared_task
from django.utils import timezone

from common.utils import get_logger
from system.utils.webhook import MAX_ATTEMPTS, RETRY_BASE_SECONDS, RETRY_MAX_SECONDS, decrypt_secret, sign_payload

logger = get_logger(__name__)

DELIVER_TIMEOUT = 10


def _default_client():
    import requests

    return requests


def _post(client, url, body: bytes, headers: dict):
    """POST 原始字节体；返回 (status_code, body_text)。网络异常转 (0, msg)。"""
    try:
        response = client.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            timeout=DELIVER_TIMEOUT,
        )
        try:
            text = response.text[:500]
        except Exception:  # noqa: BLE001
            text = ""
        return response.status_code, text
    except Exception as exc:  # noqa: BLE001 网络异常与拒绝同语义
        return 0, str(exc)[:500]


@shared_task(bind=True, max_retries=0, acks_late=True)
def deliver_webhook(self, delivery_id: str):
    """投递一次；失败按指数退避重派，耗尽置 exhausted 并告警。"""
    from system.models.webhook import WebhookDelivery
    from system.notifications import WebhookFailedMessage

    delivery = WebhookDelivery.objects.select_related("subscription").filter(pk=delivery_id).first()
    if delivery is None:
        logger.warning("webhook delivery missing: %s", delivery_id)
        return 0
    if delivery.status == "success":
        return 1

    subscription = delivery.subscription
    started = time.monotonic()
    body = json.dumps(delivery.payload, ensure_ascii=False, default=str).encode("utf-8")
    secret = decrypt_secret(subscription.secret)
    signature, timestamp = sign_payload(secret, body)
    headers = {
        "X-Xadmin-Event": delivery.event,
        "X-Xadmin-Delivery": str(delivery.pk),
        "X-Xadmin-Timestamp": str(timestamp),
        "X-Xadmin-Signature": signature,
    }

    status_code, response_text = _post(_default_client(), subscription.url, body, headers)
    duration = round(time.monotonic() - started, 3)
    delivery.attempt += 1
    delivery.response_code = status_code or None
    delivery.response_body = response_text
    delivery.duration = duration

    if 200 <= status_code < 300:
        delivery.status = "success"
        delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "attempt",
                "response_code",
                "response_body",
                "duration",
                "status",
                "next_retry_at",
                "updated_time",
            ]
        )
        logger.info("webhook delivered: %s -> %s in %ss", delivery.pk, subscription.name, duration)
        return 1

    delivery.status = "failed"
    if delivery.attempt >= MAX_ATTEMPTS:
        delivery.status = "exhausted"
        delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "attempt",
                "response_code",
                "response_body",
                "duration",
                "status",
                "next_retry_at",
                "updated_time",
            ]
        )
        subscription.last_failure = f"{delivery.event}: {response_text[:200]}"
        subscription.save(update_fields=["last_failure", "updated_time"])
        try:
            WebhookFailedMessage(
                {
                    "subscription": subscription.name,
                    "event": delivery.event,
                    "attempts": delivery.attempt,
                    "error": response_text[:200],
                }
            ).publish()
        except Exception:  # noqa: BLE001 告警失败不影响投递审计
            logger.warning("webhook exhausted alert failed", exc_info=True)
        logger.warning("webhook delivery exhausted: %s attempts=%s", delivery.pk, delivery.attempt)
        return 0

    countdown = min(RETRY_BASE_SECONDS * (2 ** (delivery.attempt - 1)), RETRY_MAX_SECONDS)
    delivery.next_retry_at = timezone.now() + timezone.timedelta(seconds=countdown)
    delivery.save(
        update_fields=[
            "attempt",
            "response_code",
            "response_body",
            "duration",
            "status",
            "next_retry_at",
            "updated_time",
        ]
    )
    logger.info(
        "webhook delivery failed (attempt %s), retry in %ss: %s", delivery.attempt, countdown, response_text[:120]
    )
    # 重试复用同一投递记录（attempt 累计），task_id 变化不影响幂等键（X-Xadmin-Delivery 不变）
    deliver_webhook.apply_async(kwargs={"delivery_id": str(delivery.pk)}, countdown=countdown)
    return 0
