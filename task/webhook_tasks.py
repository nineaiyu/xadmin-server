#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 投递任务：HMAC 签名 POST + 指数退避重试 + 耗尽告警。

- 幂等键 X-Xadmin-Delivery 恒为投递主键（接收方据此去重），与 celery task_id 无关；
- 每次派发走 dispatch_deliver_webhook：代际号 +1 并携带全新 task_id，
  队列中残留的旧任务（倒计时重试）到期后因代际不匹配静默失效，
  杜绝与人工 retry 并发导致的重复投递；结果落库按领取代际号条件更新；
- 失败 countdown = min(60 × 2^attempt, 3600) 指数退避，上限 5 次尝试；
- 耗尽 → 投递置 exhausted + 站内信告警超管（WebhookFailedMessage）；
- http 客户端可注入（单测离线）；投递链路任何异常不外抛（后台任务语义）。
"""

import json
import time
import uuid

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from common.utils import get_logger
from common.utils.outbound import pinned_request
from task.utils.webhook import MAX_ATTEMPTS, RETRY_BASE_SECONDS, RETRY_MAX_SECONDS, decrypt_secret, sign_payload

logger = get_logger(__name__)

DELIVER_TIMEOUT = 10


def _default_client():
    """生产路径返回 None（走固定解析连接）；测试/联调可注入 requests 兼容客户端。"""
    return None


def _post(client, url, body: bytes, headers: dict):
    """POST 原始字节体；返回 (status_code, body_text)。网络异常转 (0, msg)。

    生产路径（client=None）先做发送侧严格校验（拒绝私网/环回/link-local，
    白名单放行），再把连接目标固定为已校验 IP（防 DNS rebinding）；
    注入客户端路径（测试离线桩）保持原样调用，不做固定连接。
    """
    from task.utils.webhook import outbound_allowed_hosts

    merged_headers = {"Content-Type": "application/json", **headers}
    try:
        if client is None:
            response = pinned_request(
                "POST",
                url,
                allow_private=False,
                allow_loopback=True,
                allowed_hosts=outbound_allowed_hosts(),
                data=body,
                headers=merged_headers,
                timeout=DELIVER_TIMEOUT,
            )
        else:
            response = client.post(url, data=body, headers=merged_headers, timeout=DELIVER_TIMEOUT)
        try:
            text = response.text[:500]
        except Exception:  # noqa: BLE001 仅取审计文本，读取失败不影响投递结果判定
            text = ""
        return response.status_code, text
    except Exception as exc:  # noqa: BLE001 网络异常与拒绝同语义
        return 0, str(exc)[:500]


# 任务名显式钉住：模块迁位后默认名会变为 task.webhook_tasks.deliver_webhook，
# 既有投递重试 / 告警路由 / 队列匹配按名工作，零变化是硬约束
@shared_task(bind=True, max_retries=0, acks_late=True, name="system.webhook_tasks.deliver_webhook")
def deliver_webhook(self, delivery_id: str, generation: int | None = None):
    """投递一次；失败按指数退避重派，耗尽置 exhausted 并告警。

    generation 为派发时的代际号：与库中当前值不一致说明该任务已被更新的
    派发（人工 retry / 新一轮重试）超越，静默丢弃以防重复投递；未携带
    generation 的历史消息按有效处理（滚动升级兼容）。
    """
    from task.models.webhook import WebhookDelivery
    from task.notifications import WebhookFailedMessage

    # 领取：行锁内校验代际号并记住领取值，锁在 HTTP 投递前释放，
    # 在途期间的并发重派靠领取代际号的条件更新兜底
    with transaction.atomic():
        delivery = WebhookDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if delivery is None:
            logger.warning("webhook delivery missing: %s", delivery_id)
            return 0
        if generation is not None and delivery.generation != generation:
            logger.warning(
                "webhook delivery stale task skipped: %s task_generation=%s current=%s",
                delivery.pk,
                generation,
                delivery.generation,
            )
            return 0
        if delivery.status == "success":
            return 1
        claimed_generation = delivery.generation
        subscription = delivery.subscription
        payload = delivery.payload
        event = delivery.event

    started = time.monotonic()
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    secret = decrypt_secret(subscription.secret)
    signature, timestamp = sign_payload(secret, body)
    headers = {
        "X-Xadmin-Event": event,
        "X-Xadmin-Delivery": str(delivery.pk),
        "X-Xadmin-Timestamp": str(timestamp),
        "X-Xadmin-Signature": signature,
    }

    status_code, response_text = _post(_default_client(), subscription.url, body, headers)
    duration = round(time.monotonic() - started, 3)
    new_attempt = delivery.attempt + 1
    result_fields = {
        "attempt": F("attempt") + 1,
        "response_code": status_code or None,
        "response_body": response_text,
        "duration": duration,
        "updated_time": timezone.now(),
    }

    if 200 <= status_code < 300:
        updated = WebhookDelivery.objects.filter(pk=delivery.pk, generation=claimed_generation).update(
            status="success", next_retry_at=None, **result_fields
        )
        if not updated:
            # 期间发生重派，本任务已过期：结果作废，不覆盖新状态
            logger.warning("webhook delivery superseded, result dropped: %s", delivery.pk)
            return 0
        logger.info("webhook delivered: %s -> %s in %ss", delivery.pk, subscription.name, duration)
        return 1

    exhausted = new_attempt >= MAX_ATTEMPTS
    countdown = None if exhausted else min(RETRY_BASE_SECONDS * (2 ** (new_attempt - 1)), RETRY_MAX_SECONDS)
    updated = WebhookDelivery.objects.filter(pk=delivery.pk, generation=claimed_generation).update(
        status="exhausted" if exhausted else "failed",
        next_retry_at=None if exhausted else timezone.now() + timezone.timedelta(seconds=countdown),
        **result_fields,
    )
    if not updated:
        # 期间发生重派，本任务已过期：结果作废，不再续派重试
        logger.warning("webhook delivery superseded, result dropped: %s", delivery.pk)
        return 0

    if exhausted:
        subscription.last_failure = f"{event}: {response_text[:200]}"
        subscription.save(update_fields=["last_failure", "updated_time"])
        try:
            WebhookFailedMessage(
                {
                    "subscription": subscription.name,
                    "event": event,
                    "attempts": new_attempt,
                    "error": response_text[:200],
                }
            ).publish()
        except Exception:  # noqa: BLE001 告警失败不影响投递审计
            logger.warning("webhook exhausted alert failed", exc_info=True)
        logger.warning("webhook delivery exhausted: %s attempts=%s", delivery.pk, new_attempt)
        return 0

    logger.info("webhook delivery failed (attempt %s), retry in %ss: %s", new_attempt, countdown, response_text[:120])
    # 先落结果再递增代际派发下一轮：顺序反了会让自己的条件更新被自己的代际递增作废
    dispatch_deliver_webhook(str(delivery.pk), countdown=countdown)
    return 0


def dispatch_deliver_webhook(delivery_id: str, countdown: int | None = None) -> None:
    """投递任务派发唯一入口：事务内递增代际号，携带新代际号与全新 task_id 派发。

    所有派发点（emit 首投 / 任务内倒计时重试 / 人工 retry）统一经此派发；
    task_id 每次随机生成，不再复用投递主键（幂等键由 X-Xadmin-Delivery 头
    承载，不受影响）。eager（测试/E2E）同步执行；否则等当前事务提交后再
    投递——重置/建行与代际递增同事务落库，worker 读到的一定是已提交的
    代际号，不会把新任务误判为旧代际。
    """
    from task.models.webhook import WebhookDelivery

    with transaction.atomic():
        locked = WebhookDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if locked is None:
            logger.warning("webhook dispatch missing delivery: %s", delivery_id)
            return
        WebhookDelivery.objects.filter(pk=delivery_id).update(generation=F("generation") + 1)
        generation = locked.generation + 1
    message = {"delivery_id": str(delivery_id), "generation": generation}
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        deliver_webhook.apply(kwargs=message, task_id=uuid.uuid4().hex)
        return
    transaction.on_commit(
        lambda: deliver_webhook.apply_async(kwargs=message, task_id=uuid.uuid4().hex, countdown=countdown)
    )
