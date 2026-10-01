#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 配额读取（自 ai_usage 拆分，行为不变）。

配额来源优先级：SystemConfig 行（系统配置页）→ django settings（AI 设置页 /
config.yml）→ 0（不限）；供用量记账与并发流式信号量共用。
"""

from common.utils import get_logger

logger = get_logger(__name__)


def _quota_int(name: str) -> int:
    """配额读取：SystemConfig 行（系统配置页）→ django settings（AI 设置页 / config.yml）→ 0。

    AI 设置页（``Setting`` 通路）与 config.yml 都落到 ``django.conf.settings`` 同名属性上，
    只读 SystemConfig 会让页面配置静默失效；两者都读，任一显式配置即生效。
    读取异常按不限（0）处理，不阻断 AI 链路。
    """
    raw = None
    try:
        from common.core.config import SysConfig

        raw = getattr(SysConfig, name, None)
    except Exception:  # noqa: BLE001 配置读取异常按「未配置」继续回落
        logger.warning("read AI quota config failed: %s", name, exc_info=True)
        raw = None
    if raw in (None, "", {}):  # 无 SystemConfig 行时 get_value 返回空 dict
        from django.conf import settings

        raw = getattr(settings, name, 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        logger.warning("AI quota config is not an integer: %s=%r", name, raw)
        return 0


def quota_limits() -> dict:
    return {
        "daily_calls": _quota_int("AI_QUOTA_USER_DAILY_CALLS"),
        "daily_tokens": _quota_int("AI_QUOTA_USER_DAILY_TOKENS"),
        "concurrent_streams": _quota_int("AI_QUOTA_MAX_CONCURRENT_STREAMS"),
    }
