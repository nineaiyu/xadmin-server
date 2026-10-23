#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook 服务面：事件目录（events）+ 投递/签名/重试（delivery）。

对外统一从本包导入（``from task.services.webhook import emit_webhook_event``）；
``task.utils.webhook`` 保留为兼容 shim，旧引用不破。
"""

from task.services.webhook.delivery import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    build_payload,
    decrypt_secret,
    dispatch_deliveries,
    emit_webhook_event,
    encrypt_secret,
    loopback_allowed,
    outbound_allowed_hosts,
    send_test_delivery,
    sign_payload,
    validate_url,
)
from task.services.webhook.events import EVENT_CATALOG, event_catalog_payload, get_event_label, validate_events

__all__ = [
    "EVENT_CATALOG",
    "MAX_ATTEMPTS",
    "RETRY_BASE_SECONDS",
    "RETRY_MAX_SECONDS",
    "build_payload",
    "decrypt_secret",
    "dispatch_deliveries",
    "emit_webhook_event",
    "encrypt_secret",
    "event_catalog_payload",
    "get_event_label",
    "loopback_allowed",
    "outbound_allowed_hosts",
    "send_test_delivery",
    "sign_payload",
    "validate_events",
    "validate_url",
]
