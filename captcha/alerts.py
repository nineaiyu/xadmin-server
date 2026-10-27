#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码风险告警：窗口统计 + 去重 + 复用通知渠道。

只上报聚合指标与加盐哈希，不含账号/IP 明文；告警失败绝不影响验证主链路。
"""

from typing import Any

from django.conf import settings
from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)

ALERT_TOTAL_PREFIX = "captcha_alert_total"
ALERT_DENY_PREFIX = "captcha_alert_deny"
ALERT_SENT_PREFIX = "captcha_alert_sent"


def _enabled() -> bool:
    return bool(getattr(settings, "CAPTCHA_ALERT_ENABLED", True))


def _window() -> int:
    return max(int(getattr(settings, "CAPTCHA_ALERT_WINDOW_MINUTES", 10) or 10), 1) * 60


def _incr(key: str, ttl: int) -> int:
    try:
        value = int(cache.get(key, 0) or 0) + 1
        cache.set(key, value, ttl)
        return value
    except Exception:  # noqa: BLE001 计数不可用 = 不告警
        return 0


def _send(kind: str, info: dict[str, Any]) -> None:
    ttl = _window()
    try:
        if not cache.add(f"{ALERT_SENT_PREFIX}_{kind}", 1, ttl):
            return
    except Exception:  # noqa: BLE001
        return
    try:
        from captcha.notifications import CaptchaRiskAlertMessage

        CaptchaRiskAlertMessage({"kind": kind, **info}).publish_async()
    except Exception:  # noqa: BLE001 告警面失败不拖挂验证
        logger.debug("captcha risk alert publish failed", exc_info=True)


def record_decision(decision: str, reasons: list[str] | None = None) -> None:
    """窗口内累计决策量，拦截率越线时告警（同窗口只发一次）。"""
    if not _enabled():
        return
    ttl = _window()
    total = _incr(f"{ALERT_TOTAL_PREFIX}_{ttl}", ttl)
    denied = _incr(f"{ALERT_DENY_PREFIX}_{ttl}", ttl) if decision == "deny" else 0
    min_samples = int(getattr(settings, "CAPTCHA_ALERT_MIN_SAMPLES", 50) or 50)
    threshold = float(getattr(settings, "CAPTCHA_ALERT_DENY_RATE", 0.5) or 0.5)
    if total >= min_samples and total > 0:
        rate = (denied or int(cache.get(f"{ALERT_DENY_PREFIX}_{ttl}", 0) or 0)) / total
        if rate >= threshold:
            _send("deny_rate", {"deny_rate": round(rate, 4), "samples": total})
    if reasons and "freq_ip" in reasons:
        _send("ip_challenge_spike", {"reason": "freq_ip"})


def record_ip_spike(ip_hash: str) -> None:
    """同 IP 挑战突增（由信号层 freq_ip 命中时调用；只带哈希）。"""
    if _enabled() and ip_hash:
        _send(f"ip_{ip_hash}", {"ip_hash": ip_hash})
