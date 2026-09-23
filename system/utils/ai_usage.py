#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 用量账本与配额：逐次记账 + 三级配额（日调用次数 / 日 token / 全局并发流式）。

- **账本写入口收敛**：``tracked_chat`` / ``tracked_chat_stream`` / ``tracked_chat_tools``
  是各链路的统一包装（保持原 SDK 返回契约），记账不散落在业务代码里；
- **配额**（SysConfig 可配，0 / 缺省 = 不限，默认宽松）：
  ``AI_QUOTA_USER_DAILY_CALLS`` / ``AI_QUOTA_USER_DAILY_TOKENS`` /
  ``AI_QUOTA_MAX_CONCURRENT_STREAMS``（缓存计数信号量）；
- **超限口径**：读类链路返回可读提示并拒绝本次调用（不静默、不半执行），写类动作
  在审批/执行前置直接 fail-closed；调用量与账本按用户日汇总（60s 短缓存）。
"""

import datetime
import time

from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

# 并发流式计数键（进程间共享；带 TTL 兜底，避免异常退出后的计数泄漏永久化）
STREAM_SLOT_KEY = "ai_stream_slots"
STREAM_SLOT_TTL = 1800
# 日用量汇总缓存时长（配额判定读缓存，避免每次调用都聚合账本）
USAGE_CACHE_TTL = 60


def _quota_int(name: str) -> int:
    """配额读取（SysConfig 属性 → 内置 0=不限）。"""
    try:
        from common.core.config import SysConfig

        return max(0, int(getattr(SysConfig, name, 0) or 0))
    except Exception:  # noqa: BLE001 配置读取异常按不限处理（不阻断 AI 链路）
        logger.warning("read AI quota config failed: %s", name, exc_info=True)
        return 0


def quota_limits() -> dict:
    return {
        "daily_calls": _quota_int("AI_QUOTA_USER_DAILY_CALLS"),
        "daily_tokens": _quota_int("AI_QUOTA_USER_DAILY_TOKENS"),
        "concurrent_streams": _quota_int("AI_QUOTA_MAX_CONCURRENT_STREAMS"),
    }


def extract_tokens(usage) -> dict:
    """供应商 usage → (in, out, total) 三元组（字段缺失按 0，total 缺省用 in+out）。"""
    data = usage if isinstance(usage, dict) else {}
    tokens_in = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
    tokens_out = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
    tokens_total = int(data.get("total_tokens") or (tokens_in + tokens_out))
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "tokens_total": tokens_total}


def record_usage(
    user,
    feature: str,
    *,
    usage=None,
    duration_ms: int = 0,
    ok: bool = True,
    detail: str = "",
    profile_name: str = "",
    model: str = "",
    track: str = "",
) -> None:
    """写一条用量记录（失败只记日志，不影响业务链路）。

    ``track``（双轨对照）：仅动作草稿链路写 ``native`` / ``prompt``，
    其余链路留空；供用量端点按轨道统计成功率复核双轨策略。
    """
    from system.models.ai import AiUsageRecord

    tokens = extract_tokens(usage)
    try:
        AiUsageRecord.objects.create(
            creator=user if getattr(user, "pk", None) else None,
            feature=str(feature)[:16],
            track=str(track or "")[:16],
            profile_name=str(profile_name or "")[:64],
            model=str(model or "")[:128],
            duration_ms=max(0, int(duration_ms)),
            ok=bool(ok),
            detail=str(detail or "")[:255],
            **tokens,
        )
    except Exception:  # noqa: BLE001 记账失败不影响业务
        logger.warning("write AI usage record failed", exc_info=True)


def _usage_cache_key(user) -> str:
    return f"ai_usage_sum_{getattr(user, 'pk', 'anonymous')}_{timezone.localdate().isoformat()}"


def today_usage(user) -> dict:
    """当前用户当日用量（调用次数 / token；60s 短缓存，兼容无缓存后端）。"""
    from system.models.ai import AiUsageRecord

    key = _usage_cache_key(user)
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 缓存不可用直接查库
        cached = None
    if isinstance(cached, dict):
        return cached
    rows = AiUsageRecord.objects.filter(creator=user, created_time__date=timezone.localdate()).aggregate(
        calls=Count("pk"), tokens=Sum("tokens_total")
    )
    data = {"calls": int(rows["calls"] or 0), "tokens": int(rows["tokens"] or 0)}
    try:
        cache.set(key, data, USAGE_CACHE_TTL)
    except Exception:  # noqa: BLE001
        logger.debug("cache AI usage summary failed", exc_info=True)
    return data


def invalidate_usage_cache(user) -> None:
    try:
        cache.delete(_usage_cache_key(user))
    except Exception:  # noqa: BLE001
        logger.debug("invalidate AI usage cache failed", exc_info=True)


def quota_error(user, feature: str = "") -> str:
    """配额检查：返回空串 = 通过；否则返回可读拒绝文案（i18n）。"""
    limits = quota_limits()
    if not limits["daily_calls"] and not limits["daily_tokens"]:
        return ""
    used = today_usage(user)
    if limits["daily_calls"] and used["calls"] >= limits["daily_calls"]:
        logger.info("AI quota exceeded (calls). user:%s feature:%s", getattr(user, "pk", ""), feature)
        return str(
            _("Daily AI call quota reached ({}/{}); it resets tomorrow").format(used["calls"], limits["daily_calls"])
        )
    if limits["daily_tokens"] and used["tokens"] >= limits["daily_tokens"]:
        logger.info("AI quota exceeded (tokens). user:%s feature:%s", getattr(user, "pk", ""), feature)
        return str(
            _("Daily AI token quota reached ({}/{}); it resets tomorrow").format(used["tokens"], limits["daily_tokens"])
        )
    return ""


def stream_slots_in_use() -> int:
    try:
        return int(cache.get(STREAM_SLOT_KEY) or 0)
    except Exception:  # noqa: BLE001
        return 0


def acquire_stream_slot() -> bool:
    """全局并发流式配额（Redis 计数信号量）：超限返回 False（调用方给可读提示）。"""
    limit = quota_limits()["concurrent_streams"]
    if limit <= 0:
        return True
    try:
        cache.add(STREAM_SLOT_KEY, 0, STREAM_SLOT_TTL)
        if int(cache.get(STREAM_SLOT_KEY) or 0) >= limit:
            return False
        cache.incr(STREAM_SLOT_KEY)
        return True
    except Exception:  # noqa: BLE001 缓存异常不阻断业务（观测面 fail-open）
        logger.warning("acquire AI stream slot failed", exc_info=True)
        return True


def release_stream_slot() -> None:
    try:
        current = int(cache.get(STREAM_SLOT_KEY) or 0)
        if current > 0:
            cache.decr(STREAM_SLOT_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("release AI stream slot failed", exc_info=True)


class StreamSlot:
    """并发流式配额上下文（``with stream_slot() as ok:``）：异常路径也保证释放。"""

    def __init__(self):
        self.acquired = False

    def __enter__(self) -> bool:
        self.acquired = acquire_stream_slot()
        return self.acquired

    def __exit__(self, *exc_info):
        if self.acquired:
            release_stream_slot()
        return False


def stream_slot() -> StreamSlot:
    return StreamSlot()


# --------------------------------------------------------------------- 统一包装


def tracked_chat(user, feature: str, messages: list, client=None, track: str = "", **overrides) -> str:
    """单轮 LLM 调用 + 用量记账（保持 ``ChatCompletionsClient.chat`` 返回契约）。"""
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
    from system.utils.ai_config import active_profile_name, ai_credentials

    owned = client is None
    client = client or ChatCompletionsClient(ai_credentials())
    started = time.monotonic()
    try:
        reply = client.chat(messages, **overrides)
    except AiSdkError as exc:
        record_usage(
            user,
            feature,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=False,
            detail=str(exc),
            profile_name=active_profile_name() if owned else "",
            model=getattr(client, "model", ""),
            track=track,
        )
        raise
    record_usage(
        user,
        feature,
        usage=client.last_usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        profile_name=active_profile_name() if owned else "",
        model=getattr(client, "model", ""),
        track=track,
    )
    invalidate_usage_cache(user)
    return reply


def tracked_chat_tools(
    user, feature: str, messages: list, tools: list, client=None, track: str = "", **overrides
) -> dict:
    """原生 function calling 调用 + 用量记账（保持 ``chat_tools`` 返回契约）。"""
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
    from system.utils.ai_config import active_profile_name, ai_credentials

    owned = client is None
    client = client or ChatCompletionsClient(ai_credentials())
    started = time.monotonic()
    try:
        result = client.chat_tools(messages, tools, **overrides)
    except AiSdkError as exc:
        record_usage(
            user,
            feature,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=False,
            detail=str(exc),
            profile_name=active_profile_name() if owned else "",
            model=getattr(client, "model", ""),
            track=track,
        )
        raise
    record_usage(
        user,
        feature,
        usage=client.last_usage,
        duration_ms=int((time.monotonic() - started) * 1000),
        profile_name=active_profile_name() if owned else "",
        model=getattr(client, "model", ""),
        track=track,
    )
    invalidate_usage_cache(user)
    return result


def tracked_chat_stream(user, feature: str, client, messages: list, track: str = "", **overrides):
    """流式 LLM 调用 + 结束时记账（逐事件透传 ``{type, text}``，行为与 SDK 一致）。

    流式增量不改变调用方处理：产出结束后按累计用量记账；失败路径同样记账（ok=False）。
    """
    from common.sdk.ai.chat import AiSdkError
    from system.utils.ai_config import active_profile_name

    # with 覆盖整个生成器生命周期：异常/中断路径也释放并发信号量
    with stream_slot() as acquired:
        if not acquired:
            # 全局并发流式上限：给可读提示而不是静默排队
            raise AiSdkError("Too many concurrent AI streams, please retry later")
        started = time.monotonic()
        try:
            yield from client.chat_stream(messages, **overrides)
        except AiSdkError as exc:
            record_usage(
                user,
                feature,
                duration_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                detail=str(exc),
                profile_name=active_profile_name(),
                model=getattr(client, "model", ""),
                track=track,
            )
            raise
        record_usage(
            user,
            feature,
            usage=client.last_usage,
            duration_ms=int((time.monotonic() - started) * 1000),
            profile_name=active_profile_name(),
            model=getattr(client, "model", ""),
            track=track,
        )
        invalidate_usage_cache(user)


# --------------------------------------------------------------------- 汇总口径


def usage_summary(days: int = 7, feature: str = "") -> dict:
    """用量汇总（端点用）：按天 / 按链路 / Top 用户 + 合计。"""
    from system.models.ai import AiUsageRecord

    days = max(1, min(int(days or 7), 365))
    since = timezone.now() - datetime.timedelta(days=days)
    rows = AiUsageRecord.objects.filter(created_time__gte=since)
    if feature:
        rows = rows.filter(feature=feature)
    totals = rows.aggregate(calls=Count("pk"), tokens=Sum("tokens_total"), failed=Count("pk", filter=Q(ok=False)))
    by_day = list(
        rows.annotate(day=TruncDate("created_time"))
        .values("day")
        .annotate(calls=Count("pk"), tokens=Sum("tokens_total"))
        .order_by("day")
    )
    by_feature = list(rows.values("feature").annotate(calls=Count("pk"), tokens=Sum("tokens_total")).order_by("-calls"))
    # 双轨对照：仅动作草稿链路带轨道标记（native / prompt），其余为空不在本表出现
    by_track = list(
        rows.exclude(track="")
        .values("track")
        .annotate(calls=Count("pk"), failed=Count("pk", filter=Q(ok=False)), tokens=Sum("tokens_total"))
        .order_by("-calls")
    )
    top_users = list(
        rows.exclude(creator__isnull=True)
        .values("creator__username")
        .annotate(calls=Count("pk"), tokens=Sum("tokens_total"))
        .order_by("-tokens")[:10]
    )
    return {
        "days": days,
        "total_calls": int(totals["calls"] or 0),
        "total_tokens": int(totals["tokens"] or 0),
        "failed": int(totals["failed"] or 0),
        "by_day": [
            {
                "day": row["day"].isoformat() if hasattr(row["day"], "isoformat") else str(row["day"]),
                "calls": int(row["calls"] or 0),
                "tokens": int(row["tokens"] or 0),
            }
            for row in by_day
        ],
        "by_feature": [
            {"feature": row["feature"], "calls": int(row["calls"] or 0), "tokens": int(row["tokens"] or 0)}
            for row in by_feature
        ],
        "by_track": [
            {
                "track": row["track"],
                "calls": int(row["calls"] or 0),
                "failed": int(row["failed"] or 0),
                "tokens": int(row["tokens"] or 0),
                "success_rate": round(
                    (1 - int(row["failed"] or 0) / max(int(row["calls"] or 0), 1)) * 100,
                    1,
                ),
            }
            for row in by_track
        ],
        "top_users": [
            {
                "username": row["creator__username"] or "",
                "calls": int(row["calls"] or 0),
                "tokens": int(row["tokens"] or 0),
            }
            for row in top_users
        ],
        "quota": quota_limits(),
        "stream_slots": stream_slots_in_use(),
    }
