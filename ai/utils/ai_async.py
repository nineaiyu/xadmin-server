#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 流式链路异步化（S7 根治②）：用量记账 + RAG 问答的 async 对偶实现。

- ``tracked_chat_stream_async``：``ai_usage.tracked_chat_stream`` 的异步对偶——
  并发信号量（Redis INCR/DECR，微秒级直接同步调用）+ 结束时用量记账（DB 写经
  ``sync_to_async`` 落回 Django 线程本地连接）；
- ``ask_stream_async``：``ai.ask_stream`` 的异步对偶（messages/sources 由
  ``prepare_ask`` 同步装配后传入，事件契约逐字段一致）——脱敏 ``StreamMasker``
  为纯 CPU 同步逻辑（事件循环内直接调用），LLM 增量经 ``AsyncChatCompletionsClient``
  在事件循环内 await（不再逐帧占用视图线程）。

事件契约与同步版一致（``{type, text}`` 增量 / ``done`` 终帧），视图层与前端消费面
零变化。
"""

import time
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


async def tracked_chat_stream_async(
    user: Any, feature: str, client: Any, messages: list[Any], track: str = "", **overrides: Any
) -> Any:
    """异步流式 LLM 调用 + 结束时记账（逐事件透传 ``{type, text}``）。

    流式增量不改变调用方处理：产出结束后按累计用量记账；失败路径同样记账（ok=False）。
    信号量覆盖整个生成器生命周期（异常/中断路径也释放）。
    """
    from ai.utils.ai_config import active_profile_name
    from ai.utils.ai_usage import acquire_stream_slot, invalidate_usage_cache, record_usage, release_stream_slot
    from integrations.sdk.ai.chat import AiSdkError

    if not acquire_stream_slot():
        raise AiSdkError("Too many concurrent AI streams, please retry later")
    started = time.monotonic()
    try:

        def _record(**kwargs: Any) -> None:
            record_usage(
                user,
                feature,
                profile_name=active_profile_name(),
                model=getattr(client, "model", ""),
                track=track,
                **kwargs,
            )

        try:
            async for item in client.chat_stream(messages, **overrides):
                yield item
        except AiSdkError as exc:
            await sync_to_async(_record)(
                duration_ms=int((time.monotonic() - started) * 1000), ok=False, detail=str(exc)
            )
            raise
        await sync_to_async(_record)(usage=client.last_usage, duration_ms=int((time.monotonic() - started) * 1000))
        invalidate_usage_cache(user)
    finally:
        release_stream_slot()


async def ask_stream_async(messages: list[Any], sources: list[Any], user: Any = None) -> Any:
    """问答链路（异步流式生成器）：产出事件 dict，供 ``sse_response_async`` 转发。

    与同步 ``ask_stream`` 事件契约一致：``{"type": "reasoning"|"content", "text": ...}``
    增量 + ``{"type": "done", "answer", "sources", "guard"}`` 终帧；流内失败抛可读
    ValidationError。正文与思考增量均经 ``StreamMasker`` 逐段脱敏（hold-back 防
    跨帧敏感串泄漏），done 的 answer 为脱敏后全文。
    """
    from ai.utils.ai import readable_ai_error
    from ai.utils.ai_config import ai_credentials
    from ai.utils.ai_guard import StreamMasker, guard_summary
    from integrations.sdk.ai.async_chat import AiSdkError, AsyncChatCompletionsClient

    # 凭据读取与脱敏规则加载都触 DB（激活档案 / 用户自定义脱敏规则）：在异步段
    # 会被 SynchronousOnlyOperation 拦截——经 sync_to_async 落回线程本地连接构建。
    def _build_sync() -> Any:
        return AsyncChatCompletionsClient(ai_credentials()), StreamMasker(user), StreamMasker(user)

    client, content_masker, reasoning_masker = await sync_to_async(_build_sync)()
    chunks = []
    try:
        async for item in tracked_chat_stream_async(user, "docs", client, messages):
            text = item.get("text") or ""
            if not text:
                continue
            if item.get("type") == "reasoning":
                delta = reasoning_masker.feed(text)
                if delta:
                    yield {"type": "reasoning", "text": delta}
            else:
                delta = content_masker.feed(text)
                if delta:
                    chunks.append(delta)
                    yield {"type": "content", "text": delta}
    except AiSdkError as exc:
        raise DjangoValidationError(readable_ai_error(exc)) from exc
    content_tail = content_masker.flush()
    if content_tail:
        chunks.append(content_tail)
        yield {"type": "content", "text": content_tail}
    reasoning_tail = reasoning_masker.flush()
    if reasoning_tail:
        yield {"type": "reasoning", "text": reasoning_tail}
    answer = "".join(chunks).strip()
    if not answer:
        # 只有思考没有回答（思考过长被截断）：给出可操作提示，前端保留思考面板
        raise DjangoValidationError(_("The model did not provide a final answer; please retry or switch models"))
    yield {
        "type": "done",
        "answer": answer,
        "sources": sources,
        "guard": guard_summary(
            injection=[],
            mask_hits=content_masker.hits + reasoning_masker.hits,
            output_len=len(answer),
        ),
    }
