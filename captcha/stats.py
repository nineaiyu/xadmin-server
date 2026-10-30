#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码观测大盘聚合（时间窗统计）。

只做可解释的计数与占比，不做任何可反推答案或还原个体的聚合；
全量基于 ``CaptchaLog``（不含明文 IP / UA，见 telemetry 的隐私口径）。
聚合结果按窗口小时数走 60s Redis 缓存（键前缀 ``captcha_stats_``），
页面刷新与多人查看不重复打满大表扫描。
"""

import statistics
import time
from collections import Counter
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, CharField, Count, F, Q, Value, When
from django.db.models.functions import TruncHour
from django.utils import timezone

from captcha.const import current_risk_mode

STATS_CACHE_KEY_PREFIX = "captcha_stats"
STATS_CACHE_TTL = 60
MAX_WINDOW_HOURS = 168
#: 风险分直方图分桶（与规则分值同口径：0~100 五档）
RISK_BUCKETS = (
    ("0-19", 0, 19),
    ("20-39", 20, 39),
    ("40-59", 40, 59),
    ("60-79", 60, 79),
    ("80-100", 80, 100),
)
TOP_REASON_LIMIT = 10
TOP_ENTITY_LIMIT = 5
#: 原因分布的单次采样上限：攻击窗口内 deny 行可达百万级，全量拉 JSON 会拖垮管理端
TOP_REASON_SAMPLE_LIMIT = 50000


def _cache_key(hours: int) -> str:
    return f"{STATS_CACHE_KEY_PREFIX}_{hours}"


def _pass_rate(total: int, passed: int) -> float:
    return round(passed / total, 4) if total else 0.0


def _with_effective_decision(logs: Any) -> Any:
    """shadow 档决策写在 would_decision；聚合时把它当作有效决策（否则影子期大盘为空）。"""
    return logs.annotate(
        effective_decision=Case(
            When(decision="", then=F("would_decision")),
            default=F("decision"),
            output_field=CharField(),
        )
    )


def _summary(logs: Any) -> dict[str, Any]:
    rows = {row["phase"]: row["total"] for row in logs.values("phase").annotate(total=Count("pk"))}
    verify = logs.filter(phase="verify")
    total = verify.count()
    passed = verify.filter(passed=True).count()
    denied = verify.filter(effective_decision="deny").count()
    latencies = list(verify.filter(latency_ms__gt=0).values_list("latency_ms", flat=True)[:5000])
    return {
        "challenge": rows.get("challenge", 0),
        "verify": total,
        "verify_passed": passed,
        "verify_failed": total - passed,
        "consume": rows.get("consume", 0),
        "pass_rate": _pass_rate(total, passed),
        "deny": denied,
        "deny_rate": _pass_rate(total, denied),
        "latency_p50": int(statistics.median(latencies)) if latencies else 0,
    }


def _by_type(logs: Any) -> list[dict[str, Any]]:
    rows = (
        logs.filter(phase="verify")
        .exclude(type="")
        .values("type")
        .annotate(total=Count("pk"), passed=Count("pk", filter=Q(passed=True)))
    )
    # 各题型通过率对比：某题型通过率突升 = 该题型被打穿的第一预警信号
    return [
        {
            "type": row["type"],
            "total": row["total"],
            "passed": row["passed"],
            "pass_rate": _pass_rate(row["total"], row["passed"]),
        }
        for row in rows.order_by("-total")
    ]


def _by_asset(logs: Any) -> list[dict[str, Any]]:
    """按背景资产统计通过率（资产被打穿的第一预警信号）。"""
    rows = (
        logs.filter(phase="verify")
        .exclude(asset_id="")
        .values("asset_id")
        .annotate(total=Count("pk"), passed=Count("pk", filter=Q(passed=True)))
        .order_by("-total")[:20]
    )
    return [
        {
            "asset_id": row["asset_id"],
            "total": row["total"],
            "passed": row["passed"],
            "pass_rate": _pass_rate(row["total"], row["passed"]),
        }
        for row in rows
    ]


def _by_decision(logs: Any) -> list[dict[str, Any]]:
    rows = (
        logs.filter(phase="verify")
        .exclude(effective_decision="")
        .values("effective_decision")
        .annotate(total=Count("pk"))
    )
    return [{"decision": row["effective_decision"], "total": row["total"]} for row in rows.order_by("-total")]


def _trend(logs: Any) -> list[dict[str, Any]]:
    rows = (
        logs.filter(phase="verify")
        .annotate(hour=TruncHour("ts"))
        .values("hour")
        .annotate(total=Count("pk"), passed=Count("pk", filter=Q(passed=True)))
        .order_by("hour")
    )
    return [
        {
            "hour": row["hour"].isoformat() if row["hour"] else "",
            "total": row["total"],
            "passed": row["passed"],
        }
        for row in rows
    ]


def _risk_distribution(logs: Any) -> list[dict[str, Any]]:
    """风险分直方图（仅校验期有分数的行）。

    分桶交给数据库（``CASE WHEN``）：168h 窗口百万级行时不能把全部分数拉进
    Python 逐条比较（内存与耗时都会随窗口线性增长）。
    """
    buckets = Case(
        *[When(risk_score__gte=low, risk_score__lte=high, then=Value(label)) for label, low, high in RISK_BUCKETS],
        default=Value(""),
        output_field=CharField(),
    )
    rows = (
        logs.filter(phase="verify")
        .exclude(risk_score__isnull=True)
        .annotate(bucket=buckets)
        .values("bucket")
        .annotate(total=Count("pk"))
    )
    counts = {row["bucket"]: row["total"] for row in rows}
    return [{"bucket": label, "total": counts.get(label, 0)} for label, _low, _high in RISK_BUCKETS]


def _top_reasons(logs: Any) -> list[dict[str, Any]]:
    """拦截原因 TopN（只统计 deny 行，原因来自规则 code / 处置标记）。

    最多采样最近 ``TOP_REASON_SAMPLE_LIMIT`` 行：这是 TopN 趋势而非精确计数，
    不能为了完整性把整窗 JSON 拉进 Python（大窗口下会打爆管理端内存）。
    """
    counter: Counter[str] = Counter()
    sampled = logs.filter(phase="verify", effective_decision="deny").order_by("-ts")
    for reasons in sampled.values_list("risk_reasons", flat=True)[:TOP_REASON_SAMPLE_LIMIT]:
        if isinstance(reasons, list):
            counter.update(str(reason) for reason in reasons if reason)
    return [{"reason": reason, "total": total} for reason, total in counter.most_common(TOP_REASON_LIMIT)]


def _top_entities(logs: Any, field: str) -> list[dict[str, Any]]:
    """同一维度 TopN（IP / 指纹 / UA 类；只统计 deny 行且排除空值）。"""
    rows = (
        logs.filter(phase="verify", effective_decision="deny")
        .exclude(**{field: ""})
        .values(field)
        .annotate(total=Count("pk"))
        .order_by("-total")[:TOP_ENTITY_LIMIT]
    )
    return [{field: row[field], "total": row["total"]} for row in rows]


def _policy() -> dict[str, Any]:
    """策略快照：大盘头部展示当前风险档、阈值与观测口径。"""
    from captcha import risk

    thresholds = risk.thresholds()
    return {
        "risk_mode": current_risk_mode(),
        "log_enabled": bool(getattr(settings, "CAPTCHA_LOG_ENABLED", True)),
        "retention_days": int(getattr(settings, "CAPTCHA_LOG_RETENTION_DAYS", 30) or 0),
        "thresholds": thresholds,
        "pow_enabled": bool(getattr(settings, "CAPTCHA_POW_ENABLED", True)),
        "failure_ladder": str(getattr(settings, "CAPTCHA_FAIL_STEP", "3,8,20") or ""),
    }


def _build(hours: int) -> dict[str, Any]:
    from captcha import telemetry
    from captcha.models import CaptchaLog

    since = timezone.now() - timedelta(hours=hours)
    logs = _with_effective_decision(CaptchaLog.objects.filter(ts__gte=since))
    return {
        "window_hours": hours,
        "generated_at": int(time.time()),
        "policy": _policy(),
        "summary": _summary(logs),
        "by_type": _by_type(logs),
        "by_asset": _by_asset(logs),
        "by_decision": _by_decision(logs),
        "risk_distribution": _risk_distribution(logs),
        "top_reasons": _top_reasons(logs),
        "top_ips": _top_entities(logs, "ip_hash"),
        "top_fingerprints": _top_entities(logs, "fp_hash"),
        "top_ua": _top_entities(logs, "ua_class"),
        "trend": _trend(logs),
        "telemetry": telemetry.stats(),
    }


def captcha_stats(hours: int = 24) -> dict[str, Any]:
    """时间窗聚合入口：结果走 60s 缓存；缓存不可用时直接算（观测面 fail-open）。"""
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 24
    hours = max(min(hours, MAX_WINDOW_HOURS), 1)
    data: dict[str, Any] | None
    try:
        data = cache.get(_cache_key(hours))
    except Exception:  # noqa: BLE001 大盘是旁路面：缓存故障不挡查询
        data = None
    if data is None:
        data = _build(hours)
        try:
            cache.set(_cache_key(hours), data, STATS_CACHE_TTL)
        except Exception:  # noqa: BLE001
            pass
    return data
