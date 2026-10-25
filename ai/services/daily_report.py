#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 主动日报（提案 P2）：站点观测聚合 → LLM 自然语言摘要 → 缓存供首页日报卡。

边界（与提案一致）：
- 只读聚合（审批待办 / 监控告警 / 异常请求 / 任务失败 / AI 用量），不触发任何
  写动作（守住受限动作边界）；
- 摘要经 ``tracked_chat`` 记账（feature=daily），**不落会话消息表**；
- 开关 ``AI_DAILY_REPORT_ENABLED`` 默认关：关 = 周期任务空转 + 端点只报未启用；
- LLM 失败不阻塞：摘要降级为空 + 可读原因，结构化指标照常可看；
- 指标实时聚合（count/aggregate 廉价查询），摘要只读缓存——请求路径绝无
  同步 LLM 调用。

可裁剪模块（approval / task 等）被裁时对应指标按 0 兜底，日报不因此报错。
"""

import datetime
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

# 最近一期日报缓存键（手写键带命名空间前缀；站点级无用户维度，TTL 即失效面）
REPORT_CACHE_KEY = "ai_daily_report_latest"
REPORT_CACHE_TTL = 48 * 3600
# 结构化指标短缓存：首页是访问最密集页面，5 组聚合不必每个请求都跑一遍
METRICS_CACHE_KEY = "ai_daily_report_metrics"
METRICS_CACHE_TTL = 120
# 指标窗口：与监控事件面板同口径取近 24h
METRIC_WINDOW = datetime.timedelta(hours=24)

# 异常项点击直达路径（与菜单种子同源；前端 router.push 用）
REPORT_LINKS = {
    "approvals": "/approval/index",
    "monitor": "/system/monitor/index",
    "operation_log": "/system/logs/operation/index",
    "tasks": "/system/celery/logs/index",
    "ai_usage": "/integration/ai/index",
}


def daily_report_enabled() -> bool:
    """日报开关（AI 配置页热更新）：独立于 AI_ASSISTANT_ENABLED 的子功能闸。"""
    return bool(getattr(settings, "AI_DAILY_REPORT_ENABLED", False))


def _safe(label: str, fn: Any, default: Any) -> Any:
    """单指标聚合兜底：任何异常（含模块被裁的 ImportError）按默认值并留日志。"""
    try:
        return fn() or default
    except Exception:  # noqa: BLE001 单指标失败不拖垮整份日报
        logger.warning("AI daily report metric %s failed", label, exc_info=True)
        return default


def collect_metrics() -> dict[str, Any]:
    """结构化指标聚合（全部只读计数；字段名即前端日报卡展示键）。"""
    from system.services.monitor.events import alert_counts, collect_error_events, collect_task_events

    def approval_pending() -> int:
        from approval.models.approval_request import ApprovalRequest

        typed_count: int = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).count()
        return typed_count

    def ai_usage() -> dict[str, int]:
        from django.db.models import Count, Sum

        from ai.models.ai import AiUsageRecord

        since = timezone.now() - METRIC_WINDOW
        rows = AiUsageRecord.objects.filter(created_time__gte=since).aggregate(
            calls=Count("pk"), tokens=Sum("tokens_total")
        )
        return {"ai_calls_24h": int(rows["calls"] or 0), "ai_tokens_24h": int(rows["tokens"] or 0)}

    counts: dict[str, Any] = _safe("alert_counts", alert_counts, {})
    usage = ai_usage()
    return {
        "approval_pending": _safe("approval_pending", approval_pending, 0),
        "monitor_firing": _safe("monitor_firing", lambda: counts.get("firing"), 0),
        "error_requests_24h": _safe(
            "error_requests_24h", lambda: collect_error_events(range_key="24h", limit=1).get("total"), 0
        ),
        "task_failed_24h": _safe(
            "task_failed_24h", lambda: collect_task_events(range_key="24h", limit=1).get("total"), 0
        ),
        **usage,
    }


def cached_metrics() -> dict[str, Any]:
    """指标短缓存（首页高频读取）：TTL 内复用上轮聚合，过期或缓存不可用即回源。"""
    try:
        cached = cache.get(METRICS_CACHE_KEY)
    except Exception:  # noqa: BLE001 缓存不可用：直接回源，不影响可用性
        logger.debug("cache read AI daily report metrics failed", exc_info=True)
        return collect_metrics()
    if isinstance(cached, dict):
        return cached
    metrics = collect_metrics()
    try:
        cache.set(METRICS_CACHE_KEY, metrics, METRICS_CACHE_TTL)
    except Exception:  # noqa: BLE001 写缓存失败只影响下轮命中
        logger.debug("cache write AI daily report metrics failed", exc_info=True)
    return metrics


def _build_messages(metrics: dict[str, Any]) -> list[dict[str, str]]:
    """摘要 prompt：指标清单 + 输出纪律（先异常、带数字、纯句子、不编造）。"""
    from django.utils.translation import get_language

    stats = "\n".join(f"- {key}: {value}" for key, value in sorted(metrics.items()))
    system = (
        "You are the xadmin operations reporter. Summarize the site metrics below into a daily report. "
        "Rules: reply in the language of locale '{lang}'; at most 5 sentences; lead with anomalies "
        "(non-zero errors, alerts, failed tasks or a large approval backlog), then one closing sentence "
        "on overall health; quote the concrete numbers; plain sentences only, no markdown headings, "
        "bullet lists or code fences; never invent facts beyond the given metrics."
    ).format(lang=get_language() or "zh-hans")
    user = (
        "Site metrics: error requests / failed tasks / AI usage cover the last 24 hours; "
        "approval pending and monitor firing are current values.\n" + stats
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def summarize_metrics(metrics: dict[str, Any]) -> str:
    """LLM 摘要（结构化链路客户端 + 用量记账；user=None = 系统侧生成）。"""
    from ai.models.ai import AiUsageRecord
    from ai.services.assistant.ai_config import structured_chat_client
    from ai.services.assistant.ai_usage import tracked_chat

    client, max_tokens = structured_chat_client()
    return tracked_chat(
        None, str(AiUsageRecord.Feature.DAILY), _build_messages(metrics), client=client, max_tokens=max_tokens
    )


def generate_daily_report() -> dict[str, Any]:
    """生成并缓存一期日报（周期任务入口；开关关闭时空转返回空 dict）。"""
    if not daily_report_enabled():
        return {}
    metrics = collect_metrics()
    payload: dict[str, Any] = {
        "metrics": metrics,
        "summary": "",
        "summary_error": "",
        "generated_at": timezone.now().isoformat(),
    }
    try:
        payload["summary"] = summarize_metrics(metrics)
    except Exception as exc:  # noqa: BLE001 摘要失败不阻塞指标可用性
        logger.warning("AI daily report summarize failed", exc_info=True)
        payload["summary_error"] = str(exc)[:255]
    try:
        cache.set(REPORT_CACHE_KEY, payload, REPORT_CACHE_TTL)
        # 定时任务已聚合过一次：顺手刷新指标短缓存，首页首访不必再跑一遍
        cache.set(METRICS_CACHE_KEY, metrics, METRICS_CACHE_TTL)
    except Exception:  # noqa: BLE001 缓存不可用：仅本轮无摘要，指标仍实时可看
        logger.debug("cache AI daily report failed", exc_info=True)
    return payload


def daily_report_payload() -> dict[str, Any]:
    """首页日报卡数据：指标（短缓存）+ 最近一期摘要（缓存，无则空串）。"""
    enabled = daily_report_enabled()
    data: dict[str, Any] = {"enabled": enabled, "links": REPORT_LINKS}
    if not enabled:
        return data
    try:
        cached = cache.get(REPORT_CACHE_KEY)
    except Exception:  # noqa: BLE001 缓存不可用降级为「摘要未生成」
        cached = None
    report = cached if isinstance(cached, dict) else {}
    data.update(
        summary=str(report.get("summary") or ""),
        summary_error=str(report.get("summary_error") or ""),
        generated_at=str(report.get("generated_at") or ""),
    )
    data["metrics"] = cached_metrics()
    return data
