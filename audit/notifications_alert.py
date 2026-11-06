#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""敏感操作告警（操作日志命中规则后按用户节流发系统消息，自 notifications 拆分，行为不变）。"""

import hashlib
import re
from typing import Any

from audit.notifications import (
    SensitiveOperationMessage,
)
from common.utils import get_logger
from common.utils.timezone import local_now_display

logger = get_logger(__name__)


SENSITIVE_ALERT_THROTTLE_SECONDS = 60


def maybe_alert_sensitive_operation(info: dict[str, Any]) -> None:
    """敏感操作命中判定 + 节流告警（由操作日志中间件在日志落库后调用）。

    方法清单（SysConfig.SENSITIVE_OPERATION_METHODS，默认 ["DELETE"]）与路径正则
    清单（SENSITIVE_OPERATION_PATHS，默认空）AND 组合；同一 用户+方法+路径
    60 秒内只告警一次（按用户维度节流：任一用户触发不抑制他人的告警）；
    投递失败回滚节流位，下次事件仍可告警。任何异常都不影响请求响应。
    """
    from django.core.cache import cache

    from common.core.config import SysConfig

    methods = SysConfig.SENSITIVE_OPERATION_METHODS
    method = info.get("method")
    if methods and methods != "ALL" and method not in methods:
        return
    paths = SysConfig.SENSITIVE_OPERATION_PATHS
    path = info.get("path") or ""
    if paths:
        try:
            matched = any(re.search(pattern, path) for pattern in paths if pattern)
        except re.error:
            # 管理员配置了非法正则：跳过路径过滤并在本函数内消化告警，
            # 避免把 re.error 抛回中间件造成每个命中请求一条带堆栈的 warning
            logger.warning("sensitive operation alert skipped: invalid path regex %s", paths)
            return
        if not matched:
            return

    # 节流键含操作人（creator_id）：敏感操作按「谁在做」归集，全局键会让
    # 任一用户的告警抑制同路径所有人的告警
    user_id = info.get("creator_id") or ""
    digest = hashlib.md5(f"{user_id}:{method}:{path}".encode()).hexdigest()
    throttle_key = f"sensitive_op_alert_{digest}"
    if not cache.add(throttle_key, 1, SENSITIVE_ALERT_THROTTLE_SECONDS):
        return
    try:
        SensitiveOperationMessage(
            {
                "module": info.get("module"),
                "path": path,
                "method": method,
                "ipaddress": info.get("ipaddress"),
                "created_time": local_now_display(),
            }
        ).publish(is_async=True)
    except Exception:
        logger.warning("send sensitive operation alert failed", exc_info=True)
        # 投递失败回滚节流位：不回滚会让该用户该路径在 60s 窗口内静默
        try:
            cache.delete(throttle_key)
        except Exception:  # noqa: BLE001 缓存不可用时不影响主流程
            logger.warning("rollback sensitive alert throttle key failed", exc_info=True)
    # 出站 Webhook：敏感操作事件（emit 全程吞异常）
    from task.services import emit_webhook_event

    emit_webhook_event("security.sensitive_operation", info or {})
