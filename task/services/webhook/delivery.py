#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook 投递服务：发射口、签名、URL 校验与重试常量。

安全口径：
- `emit_webhook_event` 全程吞异常——Webhook 任何故障不得影响宿主动作；
- secret 沿用 Setting 的 signer 值级加密（落库密文、投递时解密）；
- 签名 GitHub 风格：`sha256=HMAC(secret, "{timestamp}.{raw_body}")`，
  timestamp 参与签名防重放（接收方建议 5 分钟窗口校验）；
- URL 在写入侧校验（https 强制，loopback http 例外供联调）并做出站地址归属校验
  （SSRF 防护：私网/环回/link-local 拒绝，`OUTBOUND_ALLOWED_HOSTS` 白名单可放行）；
- 投递侧固定解析结果连接（`packages/xadmin-common/common/utils/outbound.py::pinned_request`），
  在发送前再次校验，消除 DNS rebinding 窗口。
"""

import hashlib
import hmac
import time
from typing import Any

from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.base.utils import signer
from common.utils import get_logger
from common.utils.outbound import validate_outbound_config_url
from task.services.webhook.events import EVENT_CATALOG

logger = get_logger(__name__)

# 重试策略：countdown = min(60 × 2^attempt, 3600)，上限 5 次尝试
MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def outbound_allowed_hosts() -> tuple[Any, ...]:
    """出站白名单（逗号分隔的域名/IP，默认空 = 不启用）：Webhook 私网目标的放行途径。"""
    from common.utils.outbound import outbound_allowed_hosts as _load_allowed_hosts

    return _load_allowed_hosts()


def loopback_allowed() -> bool:
    """环回目标（127.0.0.1/localhost）是否放行：写入侧与发送侧共用的同一口径。

    生产默认 fail-closed（跟随 DEBUG=False）：持订阅权限的账号不得借投递通道
    探测本机内部服务；本地联调（DEBUG=true）与测试栈（显式配置）放开。
    两边必须同源，否则会出现「订阅写得进、投递发不出」的割裂。
    """
    from django.conf import settings

    return bool(getattr(settings, "WEBHOOK_ALLOW_LOOPBACK", settings.DEBUG))


def validate_url(url: str) -> str:
    """写入侧 URL 校验：https 强制（DEBUG 下 loopback http 联调例外）+ 字面量地址归属校验。

    - 域名写入侧不解析（可能尚未上线/仅内网 DNS 可见/被本地 DNS 屏蔽）——
      归属校验统一在发送侧严格执行（`pinned_request` 解析校验 + 固定解析结果连接）；
    - http 仅 DEBUG 下的 loopback 例外（本地联调）；Webhook 接收端不接受白名单
      http 目标——白名单仅用于放行发送侧的私网地址归属校验（内网自建接收端须登记）；
    - IP 字面量的私网/环回/link-local 目标（非 loopback http 联调例外）写入即拒绝。

    与发送侧同口径（webhook_tasks._post 的 allow_loopback）：生产环境写入与投递
    都不放行环回，避免订阅权限被用于探测本机内部服务。
    """
    return validate_outbound_config_url(
        url,
        allowed_hosts=outbound_allowed_hosts(),
        allow_http_whitelist=False,
        allow_loopback=loopback_allowed(),
        scheme_message=_("Webhook url must use https (loopback http is allowed for local testing)"),
    )


def encrypt_secret(secret: str) -> str:
    # signer.encrypt 返回 base64 bytes，落库前转 str
    return signer.encrypt(str(secret)).decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    try:
        return signer.decrypt(encrypted)
    except Exception:  # noqa: BLE001 密文损坏按无密钥处理（签名失败即可观测）
        logger.warning("webhook secret decrypt failed")
        return ""


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> tuple[Any, ...]:
    """返回 (signature_header_value, timestamp)。"""
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(str(secret).encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}", ts


def build_payload(event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造投递外壳：``{event, schema_version, occurred_at, data}``（契约版本取自事件目录）。"""
    contract = EVENT_CATALOG.get(event) or {}
    return {
        "event": event,
        "schema_version": contract.get("version", 1),
        "occurred_at": timezone.now().isoformat(),
        "data": data or {},
    }


def dispatch_deliveries(deliveries: Any) -> None:
    """把已落库的投递交给异步任务（调用方负责异常口径）。"""
    from task.webhook_tasks import dispatch_deliver_webhook

    for delivery in deliveries:
        dispatch_deliver_webhook(str(delivery.pk))


def send_test_delivery(subscription: Any) -> tuple[bool, Any]:
    """对指定订阅建一条 ping 测试投递：只测当前行，不广播给其他订阅者。

    与 ``emit_webhook_event`` 的差别：不按 ``events`` 过滤——即使当前订阅没勾选
    ``webhook.ping``，测试按钮也应能验证它的落库配置（URL / secret / 启用状态）。

    :return: ``(是否已派发, 可直接展示给操作者的原因)``；未派发时只做前置校验，
        不为无效订阅留下一条注定失败的投递记录。
    """
    from django.core.exceptions import ValidationError as DjangoValidationError

    from task.models.webhook import WebhookDelivery

    if not subscription.is_active:
        return False, _("Subscription is inactive, enable it before testing")
    if not str(subscription.url or "").strip():
        return False, _("Subscription url is empty, configure it before testing")
    try:
        validate_url(subscription.url)
    except DjangoValidationError as exc:
        return False, _("Subscription url is not deliverable: {}").format("; ".join(exc.messages))
    if not decrypt_secret(subscription.secret or ""):
        return False, _("Subscription secret cannot be decrypted, set it again before testing")
    delivery = WebhookDelivery.objects.create(
        subscription=subscription,
        event="webhook.ping",
        payload=build_payload("webhook.ping", {"subscription": subscription.name}),
    )
    dispatch_deliveries([delivery])
    return True, _("Test event dispatched, check the delivery audit page")


def emit_webhook_event(event: str, data: dict[str, Any]) -> int:
    """事件发射唯一入口：为每个订阅该事件的 active 订阅创建投递并派发任务。

    全程吞异常：Webhook 链路任何故障只记日志，绝不影响宿主动作。
    :return: 创建的投递数（仅测试/观测用途）。
    """
    try:
        if event not in EVENT_CATALOG:
            logger.warning("emit unknown webhook event: %s", event)
            return 0
        from task.models.webhook import WebhookDelivery, WebhookSubscription

        # events 为 JSON 列表：支持 JSON contains 的引擎把事件匹配下推 DB
        # （jsonb @> / JSON_CONTAINS），只拉命中的订阅；SQLite/Oracle 无该
        # lookup，保留内存过滤兜底。订阅量为管理面个位数且事件按业务动作触发
        # （非每请求热路径），不再叠加进程级缓存——缓存 TTL 会让跨进程的订阅
        # 增删延迟生效，投递语义（订阅即收、删订即停）保持精确不变。
        active_subscriptions = WebhookSubscription.objects.filter(is_active=True)
        if connection.vendor in ("postgresql", "mysql"):
            subscriptions = list(active_subscriptions.filter(events__contains=[event]))
        else:
            subscriptions = [sub for sub in active_subscriptions if event in (sub.events or [])]
        contract = EVENT_CATALOG.get(event) or {}
        # 契约校验只告警不阻断：宿主链路永不受 webhook 契约缺陷影响（守护测试兜底）
        missing = [
            name
            for name, spec in (contract.get("fields") or {}).items()
            if spec.get("required") and name not in (data or {})
        ]
        if missing:
            logger.warning("webhook event %s missing required fields: %s", event, ", ".join(missing))
        payloads = [
            WebhookDelivery(subscription=sub, event=event, payload=build_payload(event, data)) for sub in subscriptions
        ]
        deliveries = WebhookDelivery.objects.bulk_create(payloads)
        dispatch_deliveries(deliveries)
        return len(deliveries)
    except Exception:  # noqa: BLE001 发射口绝不打断宿主动作
        logger.warning("emit webhook event failed: %s", event, exc_info=True)
        return 0
