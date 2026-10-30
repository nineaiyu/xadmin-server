#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码观测层遥测收口（全链路留痕的唯一入口）。

写入与主链路物理隔离：任何遥测异常（含 Celery 不可用）都不得影响验证结果
——``emit`` 全路径 catch-all，投递失败降级进程内有界缓冲，下次投递时批量补发，
缓冲溢出丢最旧并计数。采样口径：失败 100%，成功按
``CAPTCHA_LOG_SUCCESS_SAMPLE_RATE``（默认 100%）。

隐私最小化：IP 只落网段无关的加盐哈希（盐随票据密钥轮换，轮换即切断历史关联）；
不落明文 UA 全文，只落 UA 类别与浏览器族。
"""

import contextvars
import random
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from common.utils import get_logger

logger = get_logger(__name__)

#: 进程内降级缓冲上限：溢出丢最旧（遥测完整性让位于主链路内存安全）
FALLBACK_BUFFER_LIMIT = 2000
#: 单请求缓冲上限：达到即投递（异常请求不把整窗事件攒到请求结束）
BATCH_SIZE = 20
#: 请求级事件缓冲（同一请求的 challenge/verify/decision 合并为一次 Celery 投递）
_REQUEST_BUFFER: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "captcha_telemetry_buffer", default=None
)
_FIELDS = (
    "phase",
    "scene",
    "action",
    "mode",
    "type",
    "difficulty",
    "asset_id",
    "decision",
    "would_decision",
    "risk_score",
    "risk_reasons",
    "passed",
    "attempts",
    "latency_ms",
    "ip_hash",
    "fp_hash",
    "ua_class",
    "browser",
    "env_version",
    "pow_bits",
    "key_id",
)
_lock = threading.Lock()
_fallback: list[dict[str, Any]] = []
_stats = {"emitted": 0, "dispatched": 0, "buffered": 0, "dropped": 0}

_MOBILE_RE = re.compile(r"Mobile|Android|iPhone|iPad|iPod", re.I)
_BROWSER_RE = re.compile(r"(HeadlessChrome|Edg/|OPR/|Firefox/|Chrome/|Safari/)[\w.]*", re.I)


def telemetry_enabled() -> bool:
    return bool(getattr(settings, "CAPTCHA_LOG_ENABLED", True))


def hash_ip(ip: str) -> str:
    """IP 加盐哈希（与风控/票据/处置共用 ``captcha.signals.hash_ip`` 单一实现）。

    两套哈希会让观测日志的 TopN 与处置键对不上（无法互证），因此这里只做转发，
    盐与网段口径一律由信号层决定。
    """
    from captcha.signals import hash_ip as _hash_ip

    return _hash_ip(ip)


def classify_ua(ua: str) -> str:
    """UA 大类：mobile / desktop / headless / other（不落明文全文）。"""
    if not ua:
        return ""
    if re.search(r"Headless", ua, re.I):
        return "headless"
    if _MOBILE_RE.search(ua):
        return "mobile"
    if "Mozilla" in ua or "Chrome" in ua or "Safari" in ua:
        return "desktop"
    return "other"


def browser_family(ua: str) -> str:
    """浏览器族短标签（如 Chrome/126、Firefox/128；空 UA 返回空串）。"""
    match = _BROWSER_RE.search(ua or "")
    return match.group(0)[:64] if match else ""


def stats() -> dict[str, int]:
    """遥测自身健康度（投递 / 缓冲 / 丢弃计数），供诊断与大盘引用。"""
    with _lock:
        return {**_stats, "fallback_pending": len(_fallback)}


def emit(**event: Any) -> None:
    """登记一条验证码事件；任何内部异常静默吞掉（不得影响验证主链路）。"""
    try:
        _emit_inner(**event)
    except Exception:  # noqa: BLE001 遥测面 catch-all
        logger.debug("captcha telemetry emit failed", exc_info=True)


def begin_request() -> None:
    """标记进入一次验证码请求：其间的遥测事件合并为一次投递。"""
    _REQUEST_BUFFER.set([])


def end_request() -> None:
    """请求结束（含异常路径）：冲刷本请求缓冲；未成对调用 begin 时为 no-op。"""
    pending = _REQUEST_BUFFER.get()
    _REQUEST_BUFFER.set(None)
    _flush(pending or [])


def _emit_inner(**event: Any) -> None:
    if not telemetry_enabled():
        return
    passed = bool(event.get("passed", False))
    if passed and random.random() >= _success_sample_rate():
        return
    record = {key: event.get(key) for key in _FIELDS if key in event}
    record["ts"] = time.time()
    _stats["emitted"] += 1
    pending = _REQUEST_BUFFER.get()
    if pending is None:
        # 非请求上下文（任务 / 单测）：保持即时投递语义
        _flush([record])
        return
    pending.append(record)
    if len(pending) >= BATCH_SIZE:
        _REQUEST_BUFFER.set([])
        _flush(pending)


def _flush(events: list[dict[str, Any]]) -> None:
    """投递一批事件；同时补发降级缓冲（队列恢复后自动追平）。"""
    if not events:
        return
    with _lock:
        pending = _fallback.copy()
        _fallback.clear()
    batch = pending + events
    if _dispatch(batch):
        _stats["dispatched"] += len(batch)
    else:
        _rebuffer(batch)


def _success_sample_rate() -> float:
    rate = getattr(settings, "CAPTCHA_LOG_SUCCESS_SAMPLE_RATE", 1.0)
    try:
        return min(max(float(rate), 0.0), 1.0)
    except (TypeError, ValueError):
        return 1.0


def _dispatch(events: list[dict[str, Any]]) -> bool:
    """投递到 Celery 批量落库任务；投递失败返回 False（调用方降级缓冲）。"""
    try:
        from captcha.tasks import flush_captcha_logs

        flush_captcha_logs.delay(events)
        return True
    except Exception:  # noqa: BLE001 队列不可用 = 降级缓冲
        return False


def _rebuffer(events: list[dict[str, Any]]) -> None:
    with _lock:
        _fallback.extend(events)
        overflow = len(_fallback) - FALLBACK_BUFFER_LIMIT
        if overflow > 0:
            del _fallback[:overflow]
            _stats["dropped"] += overflow
        _stats["buffered"] += len(events)


def flush_fallback() -> int:
    """手动冲刷降级缓冲（诊断 / 测试用）；返回本次成功补发条数。"""
    with _lock:
        drained = _fallback.copy()
        _fallback.clear()
    if drained and _dispatch(drained):
        _stats["dispatched"] += len(drained)
        return len(drained)
    if drained:
        _rebuffer(drained)
    return 0


def build_log_row(event: dict[str, Any]) -> Any:
    """事件字典 -> CaptchaLog 模型行（ts epoch -> aware datetime）。"""
    from captcha.models import CaptchaLog

    fields = {key: event.get(key) for key in _FIELDS if key in event}
    ts = event.get("ts") or time.time()
    fields["ts"] = datetime.fromtimestamp(float(ts), tz=UTC)
    return CaptchaLog(**fields)
