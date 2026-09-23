#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 动作执行幂等：同一意图在 TTL 内重复提交返回首次执行结果。

- ``draft_id`` = 用户 + 动作序列 + 规范化参数的服务端稳定哈希（不信任前端回传）；
- TTL 10 分钟与 412 审批令牌口径对齐：连点 / 网络重试 / 双端同开都安全；
- 命中即返回首次结果并标记 ``deduplicated=True``；「确实想再执行一次」走显式
  ``force`` 通道（前端提示后由用户确认重发，不静默重复写）；
- 记录落缓存（Redis，JSON 序列化，失败不阻断业务）。
"""

import hashlib
import json

from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)

# 幂等窗口（秒）：与审批令牌 TTL 同口径
IDEMPOTENCY_TTL = 600
CACHE_PREFIX = "ai_action_idem"


def draft_id(user, action_key: str, params: dict) -> str:
    """稳定哈希：同一用户 + 同一动作 + 同一（规范化）参数 → 同一 draft_id。

    参数按键排序序列化，避免字段顺序差异导致哈希漂移；``default=str`` 兼容
    UUID / datetime / Decimal 等业务参数形态。
    """
    payload = json.dumps(
        {
            "user": str(getattr(user, "pk", "")),
            "action": str(action_key or ""),
            "params": params if isinstance(params, dict) else {},
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _cache_key(user, key: str) -> str:
    return f"{CACHE_PREFIX}:{getattr(user, 'pk', 'anonymous')}:{key}"


def find_result(user, key: str):
    """查首次执行结果（命中返回 dict，未命中 None，缓存异常按未命中处理）。"""
    try:
        raw = cache.get(_cache_key(user, key))
    except Exception:  # noqa: BLE001 缓存不可用不阻断业务
        logger.debug("read AI idempotency cache failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def store_result(user, key: str, result: dict) -> None:
    """记录首次执行结果（缓存异常/序列化异常都不影响业务返回）。"""
    try:
        cache.set(_cache_key(user, key), json.dumps(result, ensure_ascii=False, default=str), IDEMPOTENCY_TTL)
    except Exception:  # noqa: BLE001
        logger.warning("write AI idempotency cache failed", exc_info=True)


def execute_idempotent(user, action_key: str, params: dict, executor, force: bool = False) -> dict:
    """带幂等的动作执行：``executor(user, action_key, params)`` 返回 ``{ok, detail, data}``。

    幂等只作用于**成功**结果（失败允许立即重试）；``force=True`` 跳过幂等检查。
    返回结果追加 ``draft_id``；命中未强制的重复请求追加 ``deduplicated=True``。
    """
    key = draft_id(user, action_key, params)
    if not force:
        first = find_result(user, key)
        if first is not None:
            logger.info("AI action deduplicated. action:%s user:%s", action_key, getattr(user, "pk", ""))
            return {**first, "draft_id": key, "deduplicated": True}
    result = executor(user, action_key, params)
    result = {**result, "draft_id": key}
    if result.get("ok"):
        store_result(user, key, {"ok": True, "detail": result.get("detail"), "data": result.get("data") or {}})
    return result
