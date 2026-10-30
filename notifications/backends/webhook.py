#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Webhook 通知渠道：站点级转发端点。

与 email / IM 等「逐用户账号」渠道不同，webhook 把消息正文与目标用户清单整体
POST 到管理员配置的端点（``WEBHOOK_URL``），由接收端自行决定路由——适合
「通知转发到内部系统 / 值班机器人」场景。

出站走 ``common.utils.outbound.pinned_request`` 统一出口（防 SSRF / DNS
rebinding，私网目标默认拒绝），放行口径与开放平台 Webhook 投递一致
（``outbound_allowed_hosts`` 白名单）。签名与开放平台事件投递同款
（``sha256 = HMAC(secret, "{ts}." + body)``，``X-Webhook-Signature`` /
``X-Webhook-Timestamp`` 头），接收端可复用同一套验签逻辑。
"""

import hashlib
import hmac
import json
import time
from typing import Any

from django.conf import settings

from common.utils import get_logger
from notifications.backends.base import BackendBase

logger = get_logger(__name__)

#: 出站超时（秒）：与开放平台 Webhook 投递一致（连接/读各半由 requests 分摊）
DELIVER_TIMEOUT = 10

#: 转发端点的目标用户字段上限：全站广播时防 payload 膨胀（超出截断并记日志）
USERS_LIMIT = 200


def sign_webhook_payload(secret: str, body: bytes, timestamp: int | None = None) -> tuple[str, int]:
    """通知转发签名：与开放平台 sign_payload 同口径（``sha256=HMAC("{ts}." + body)``）。"""
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(str(secret).encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}", ts


class Webhook(BackendBase):
    """Webhook 转发渠道（站点级端点，无逐用户绑定）"""

    is_enable_field_in_settings = "WEBHOOK_ENABLED"

    @classmethod
    def is_enable(cls) -> Any:
        # 开与 URL 同时就绪才算可用：开关开而 URL 缺 → 渠道自动降级为不可用
        # （与 IM 渠道「开而凭据缺降级」同口径），订阅矩阵不会展示半配置渠道
        return bool(getattr(settings, cls.is_enable_field_in_settings, False)) and bool(
            getattr(settings, "WEBHOOK_URL", "")
        )

    def send_msg(self, users: Any, message: Any, subject: Any = "", **kwargs: Any) -> Any:
        from common.utils.outbound import pinned_request
        from task.services import outbound_allowed_hosts

        url = settings.WEBHOOK_URL
        if not url:
            logger.warning("Webhook notification skipped: WEBHOOK_URL not configured")
            return None
        people = list(users)
        recipients = [
            {"pk": str(user.pk), "username": user.username, "nickname": getattr(user, "nickname", "")}
            for user in people[:USERS_LIMIT]
        ]
        if len(people) > USERS_LIMIT:
            logger.warning(
                "Webhook notification recipients truncated to %s (total %s)",
                USERS_LIMIT,
                len(people),
            )
        payload = {"subject": str(subject or ""), "message": str(message or ""), "users": recipients}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = str(getattr(settings, "WEBHOOK_SECRET", "") or "")
        if secret:
            signature, timestamp = sign_webhook_payload(secret, body)
            headers["X-Webhook-Signature"] = signature
            headers["X-Webhook-Timestamp"] = str(timestamp)
        # 渠道语义「尽力而为」：失败记日志（与 IM 渠道同口径），不阻断其他渠道投递
        try:
            response = pinned_request(
                "POST",
                url,
                allow_private=False,
                allowed_hosts=outbound_allowed_hosts(),
                data=body,
                headers=headers,
                timeout=DELIVER_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 通知失败不影响发布链路
            logger.warning("Webhook notification delivery failed for %s", url, exc_info=True)
            return None
        # 4xx/5xx 由 requests 正常返回（不抛错）：端点故障必须可观测，不能静默当成功
        status_code = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status_code < 300:
            logger.warning("Webhook notification rejected by %s: status=%s", url, status_code)
        return response


backend = Webhook
