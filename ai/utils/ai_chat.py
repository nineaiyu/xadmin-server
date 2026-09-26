#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手对话持久化：助手页三入口（文档问答 / 数据查询 / 指令执行）消息流。

- 落库收口在本模块：各视图只调 ``persist_message``，序列化契约唯一（``message_payload``），
  前端历史渲染与流式 done 载荷共用同一份结构，避免"本地拼装"与"服务端存储"漂移；
- 读取按 ``(creator, feature)`` 过滤：用户只能看到自己的消息流（history 端点不
  接受用户参数）；
- 与聊天室同口径：内容与思考都按上限截断落库（``MAX_CONTENT_STORED`` /
  ``MAX_REASONING_STORED``）；空内容的兜底文案由调用方负责（如「只有思考没有
  回答」时落 no_answer 文案）——本函数不做拒绝，只截断。
"""

import json

from django.core.serializers.json import DjangoJSONEncoder

from common.utils import get_logger

logger = get_logger(__name__)

#: 单条消息内容落库上限（与动作结果/长文档回答同量级；超出截断并标记）
MAX_CONTENT_STORED = 20000
#: 思考内容落库上限（与聊天室 message/ai.py 同口径）
MAX_REASONING_STORED = 4000
#: 历史分页默认/上限
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def normalize_feature(value) -> str:
    """入口标识归一（非法值回落 docs，防御式：端点已按 feature 路由）。"""
    from ai.models.ai import AiChatMessage

    text = str(value or "").strip().lower()
    valid = {choice[0] for choice in AiChatMessage.Feature.choices}
    return text if text in valid else AiChatMessage.Feature.DOCS


def clip_content(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_CONTENT_STORED:
        return text
    return text[:MAX_CONTENT_STORED] + "…"


def clip_reasoning(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_REASONING_STORED:
        return text
    return text[:MAX_REASONING_STORED] + "…"


def persist_message(user, feature: str, role: str, content: str = "", reasoning: str = "", extra: dict = None):
    """落一条助手消息；返回消息行（失败返回 None，不阻断主链路）。

    持久化失败不影响对话本身（与审计同口径：吞异常 + 日志），但调用方在
    ``done`` 载荷里应容忍 ``message`` 缺失（前端回落本地构造）。
    """
    from ai.models.ai import AiChatMessage

    try:
        return AiChatMessage.objects.create(
            feature=normalize_feature(feature),
            role=role,
            content=clip_content(content),
            reasoning=clip_reasoning(reasoning),
            extra=_json_safe(extra or {}),
            creator=user,
            modifier=user,
            dept_belong=getattr(user, "dept", None),
        )
    except Exception:  # noqa: BLE001 对话持久化失败不影响问答/执行本身
        logger.warning("persist AI chat message failed. feature:%s role:%s", feature, role, exc_info=True)
        return None


def _json_safe(value):
    """JSON 安全化：未知类型统一转字符串（NL 结果行可能含 UUID/Decimal/datetime）。"""
    try:
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder, default=str))
    except (TypeError, ValueError):
        return {}


def message_payload(row) -> dict:
    """消息行 → 前端契约（历史与流式 done 共用）。"""
    if row is None:
        return {}
    return {
        "id": row.pk,
        "feature": row.feature,
        "role": row.role,
        "content": row.content,
        "reasoning": row.reasoning,
        "extra": row.extra or {},
        "created_time": row.created_time.isoformat() if row.created_time else "",
    }


def load_history(user, feature: str, before_id=None, limit=None) -> dict:
    """按 ``(creator, feature)`` 拉历史：时间正序返回，``has_more`` 表示还有更早的。"""
    from ai.models.ai import AiChatMessage

    try:
        size = int(limit or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    size = max(1, min(size, MAX_PAGE_SIZE))

    queryset = AiChatMessage.objects.filter(creator=user, feature=normalize_feature(feature))
    if before_id:
        try:
            queryset = queryset.filter(id__lt=int(before_id))
        except (TypeError, ValueError):
            pass
    rows = list(queryset.order_by("-id")[: size + 1])
    has_more = len(rows) > size
    rows = rows[:size]
    rows.reverse()
    return {"results": [message_payload(row) for row in rows], "has_more": has_more}


def system_error_message(user, feature: str, detail: str) -> dict:
    """流内失败的降级消息（落库 + 返回载荷）：前端按系统提示渲染。"""
    row = persist_message(user, feature, "system", content=detail, extra={"error": True})
    return message_payload(row)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "clip_content",
    "clip_reasoning",
    "load_history",
    "message_payload",
    "normalize_feature",
    "persist_message",
    "system_error_message",
]
