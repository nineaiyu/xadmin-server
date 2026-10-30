#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""兼容 shim：出站 Webhook 实现已迁至 ``task.services.webhook``。

保留本模块供二开与旧测试继续 ``from task.utils.webhook import ...``；
新增代码请直接从 ``task.services.webhook`` 导入。
"""

from task.services.webhook import (
    EVENT_CATALOG,
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    build_payload,
    decrypt_secret,
    dispatch_deliveries,
    emit_webhook_event,
    encrypt_secret,
    event_catalog_payload,
    get_event_label,
    loopback_allowed,
    outbound_allowed_hosts,
    send_test_delivery,
    sign_payload,
    validate_events,
    validate_url,
)

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
