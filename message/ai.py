#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 AI 助手：通用多轮对话 + `/kb` 知识库问答双形态。

- 通用多轮：读取本会话最近 N 条消息裁剪上下文 + 内置助手人设 → ChatCompletionsClient；
- `/kb 问题`：复用 system.utils.ai 的知识库 RAG（ask）链路，回复附引用来源（extra.sources）；
- 门禁：`AI_ASSISTANT_ENABLED` 且凭据齐全才可用（未启用/未配置统一可读降级，不静默）；
- 异常降级：LLM 失败落一条 system 消息（前端可见），REST 同时返回可读 detail；
- 会话走 REST 同步（LLM 5~60s，不占用 WS 长连接）；流式走 `ai_stream_events`（SSE，二期）。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message import chat as chat_service
from message.models import ChatMessage, ChatRoom
from message.utils import push_room_event
from system.utils.ai_chat import clip_reasoning as _clip_reasoning

logger = get_logger(__name__)

# `/kb` 前缀命令：走知识库 RAG；`/do` 前缀命令：受限动作草稿（A2）；否则通用多轮对话
KB_COMMAND = "/kb"
ACTION_COMMAND = "/do"


def is_enabled() -> bool:
    """AI 助手可用性：开关开启 + 凭据齐全（复用 system.utils.ai 单一判定）。"""
    from system.utils.ai import is_enabled as _is_enabled

    return bool(_is_enabled())


def is_kb_command(content: str) -> bool:
    """是否为知识库问答命令（`/kb` 或 `/kb 问题`）。"""
    return (content or "").strip().lower().startswith(KB_COMMAND)


def strip_kb_command(content: str) -> str:
    """去掉 `/kb` 前缀取真实问题（保留空串：由调用方给出可读提示）。"""
    return (content or "").strip()[len(KB_COMMAND) :].strip()


def is_action_command(content: str) -> bool:
    """是否为受限动作命令（`/do` 或 `/do 请求描述`）。"""
    return (content or "").strip().lower().startswith(ACTION_COMMAND)


def strip_action_command(content: str) -> str:
    """去掉 `/do` 前缀取真实请求描述（保留空串：由调用方给出可读提示）。"""
    return (content or "").strip()[len(ACTION_COMMAND) :].strip()


def action_reply(user, request_text: str) -> tuple:
    """受限动作草稿（A2）：返回 (content, extra, mode)。

    - 可执行 → content 为确认摘要，``extra.action_draft`` 携带白名单动作与规范化参数
      （前端渲染确认卡片，用户二次确认后经 execute 端点以本人身份执行）；
    - 请求不可执行/缺参数 → 返回澄清文本（mode=chat，按普通 AI 气泡渲染，不落草稿）；
    - 灰度关闭/LLM 失败 → 抛可读校验错误（调用方落 system 消息降级，不静默）。
    """
    from common.sdk.ai.chat import AiSdkError
    from system.utils.ai import native_tools_enabled, structured_chat_client
    from system.utils.ai_actions import ai_action_enabled, build_draft_prompt, draft_summary, parse_draft
    from system.utils.ai_guard import mask_text

    if not ai_action_enabled():
        raise DjangoValidationError(_("AI actions are not enabled"))
    if not request_text:
        raise DjangoValidationError(_("Please describe the request after /do"))
    if native_tools_enabled():
        # 原生 function calling 轨道（能力探测通过 + 开关开启时优先）
        from system.utils.ai_actions import native_draft_result

        try:
            result, track = native_draft_result(user, request_text)
            logger.info("chat ai action draft track: %s (user=%s)", track, getattr(user, "pk", ""))
        except AiSdkError as exc:
            logger.warning("chat ai native action draft failed: %s", exc)
            raise DjangoValidationError(_("AI service is temporarily unavailable")) from exc
    else:
        from system.utils.ai_usage import tracked_chat

        logger.info("chat ai action draft track: prompt (user=%s)", getattr(user, "pk", ""))

        try:
            # 客户端与结构化输出上限走公共入口（与助手页 action/interpret/stream 同一口径）
            client, max_tokens = structured_chat_client()
            raw = tracked_chat(
                user,
                "action",
                build_draft_prompt(user, request_text),
                client=client,
                track="prompt",  # 双轨对照：用量账本按轨道统计成功率
                max_tokens=max_tokens,
            )
            result = parse_draft(raw, user)
        except AiSdkError as exc:
            logger.warning("chat ai action draft failed: %s", exc)
            raise DjangoValidationError(_("AI service is temporarily unavailable")) from exc
    if result["kind"] == "message":
        content, __hits = mask_text(result["message"], user)
        return content, {"mode": "chat"}, "chat"
    drafts = result.get("drafts") or [result["draft"]]
    extra = {"mode": "action", "action_draft": drafts[0]}
    if len(drafts) > 1:
        # 多草稿串联：action_drafts 供前端逐项渲染确认卡片（action_draft 保留首个，兼容旧渲染）
        extra["action_drafts"] = drafts
    summary, mask_hits = mask_text(draft_summary(drafts), user)
    if mask_hits:
        extra["guard"] = {"mask_hits": mask_hits}
    return summary, extra, "action"


def history_messages(room: ChatRoom, limit: int | None = None, drop_last_user: bool = False) -> list:
    """取会话最近 N 条消息（时间正序）：用户消息 → user，AI 回复 → assistant，系统消息跳过。

    limit 缺省读配置（档案/Setting 的 AI_CONTEXT_LIMIT）；
    drop_last_user：本轮提问已先落库，裁剪上下文时去掉末条 user 消息，避免重复一轮。
    """
    if limit is None:
        from system.utils.ai import ai_context_limit

        limit = ai_context_limit()
    rows = list(ChatMessage.objects.filter(room=room).order_by("-id")[: limit * 2])
    rows.reverse()
    messages = []
    for row in rows:
        if row.is_recalled or row.message_type == ChatMessage.MessageType.SYSTEM:
            continue
        role = "assistant" if row.message_type == ChatMessage.MessageType.AI else "user"
        messages.append({"role": role, "content": row.content})
    if drop_last_user and messages and messages[-1]["role"] == "user":
        messages.pop()
    return messages[-limit:]


def build_chat_messages(room: ChatRoom, question: str) -> list:
    """通用多轮上下文：人设 + 历史（不含本轮提问）+ 本轮提问。"""
    from system.utils.ai import ai_persona

    return (
        [{"role": "system", "content": ai_persona()}]
        + history_messages(room, drop_last_user=True)
        + [{"role": "user", "content": question}]
    )


def _llm_reply(messages: list, user=None) -> tuple:
    """普通多轮：返回 ``(脱敏后文本, 脱敏命中数)``（输出护栏 + 用量记账）。"""
    from common.sdk.ai.chat import AiSdkError
    from system.utils.ai_guard import mask_text
    from system.utils.ai_usage import tracked_chat

    try:
        answer = tracked_chat(user, "chat", messages)
    except AiSdkError as exc:
        logger.warning("chat ai llm failed: %s", exc)
        raise DjangoValidationError(_("AI service is temporarily unavailable")) from exc
    return mask_text(answer, user)


def kb_answer(question: str, user=None) -> tuple:
    """知识库问答：返回 (answer, sources)；无命中/未启用转可读校验错误。

    answer 已由 ``system.utils.ai.ask`` 走输出护栏脱敏。
    """
    from system.utils.ai import ask

    result = ask(question, user=user)
    return result["answer"], result.get("sources") or []


def ai_reply_content(room: ChatRoom, question: str) -> tuple:
    """按命令分流生成回复，返回 (content, extra, mode)（输出文本统一过护栏脱敏）。"""
    from system.utils.ai_usage import quota_error

    quota = quota_error(room.owner, "chat")
    if quota:
        raise DjangoValidationError(quota)
    if is_action_command(question):
        # AI 房间归属者即发起用户（视图层已保证 room_type=ai）
        return action_reply(room.owner, strip_action_command(question))
    if is_kb_command(question):
        kb_question = strip_kb_command(question)
        if not kb_question:
            raise DjangoValidationError(_("Please provide a question after /kb, e.g. /kb how to reset password"))
        answer, sources = kb_answer(kb_question, user=room.owner)
        return answer, {"mode": "kb", "sources": sources}, "kb"
    answer, mask_hits = _llm_reply(build_chat_messages(room, question), room.owner)
    extra = {"mode": "chat"}
    if mask_hits:
        extra["guard"] = {"mask_hits": mask_hits}
    return answer, extra, "chat"


def _llm_reply_stream(messages: list, user=None):
    """流式多轮：逐段产出 ``{"type": "reasoning"|"content", "text": ...}`` 事件。

    AiSdkError 转可读校验错误（在生成器内抛出）；用量记账由 ``tracked_chat_stream`` 收口。
    """
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
    from system.utils.ai import ai_credentials
    from system.utils.ai_usage import tracked_chat_stream

    client = ChatCompletionsClient(ai_credentials())
    try:
        yield from tracked_chat_stream(user, "chat", client, messages)
    except AiSdkError as exc:
        logger.warning("chat ai llm stream failed: %s", exc)
        raise DjangoValidationError(_("AI service is temporarily unavailable")) from exc


def ai_stream_events(room: ChatRoom, question: str, question_payload: dict):
    """SSE 事件生成器（二期，含思考过程）：yield dict(event, data)，视图转 text/event-stream。

    事件序：``meta``（问题回执）→ ``reasoning``*（思考增量，思考型模型才有）→
    ``delta``*（回答增量）→ ``done``（正式消息载荷）| ``error``。
    落库与 WS 广播在此收口，与 ``ai_message`` 同口径：

    - 全程无增量即失败 → 落一条 system 降级消息（前端可见）+ error 事件；
    - 已有增量后中断 → 保留部分回答（extra.partial 记录中断原因）+ done 事件；
    - 只有思考没有回答（思考型模型思考过长被截断）→ 保留思考（extra.reasoning），
      以下落文案作内容并标记 extra.no_answer，前端展示思考过程与「未给出最终回答」。

    输出护栏：正文与思考增量均经 ``StreamMasker`` 逐段脱敏（hold-back 防
    跨帧敏感串泄漏），落库与广播用脱敏后文本；命中数写 extra.guard。
    """
    from system.utils.ai_guard import StreamMasker
    from system.utils.ai_usage import quota_error

    yield {"event": "meta", "data": {"question": question_payload}}

    chunks: list = []
    reasoning_chunks: list = []
    extra = {"mode": "chat"}
    content_masker = StreamMasker(room.owner)
    reasoning_masker = StreamMasker(room.owner)
    try:
        quota = quota_error(room.owner, "chat")
        if quota:
            raise DjangoValidationError(quota)
        if is_action_command(question):
            # `/do` 动作草稿：整段单 delta（草稿是结构化结果，无打字机语义；摘要已脱敏）
            content, extra, __ = action_reply(room.owner, strip_action_command(question))
            chunks.append(content)
            yield {"event": "delta", "data": {"delta": content}}
        elif is_kb_command(question):
            kb_question = strip_kb_command(question)
            if not kb_question:
                raise DjangoValidationError(_("Please provide a question after /kb, e.g. /kb how to reset password"))
            answer, sources = kb_answer(kb_question, user=room.owner)
            extra = {"mode": "kb", "sources": sources}
            chunks.append(answer)
            yield {"event": "delta", "data": {"delta": answer}}
        else:
            for item in _llm_reply_stream(build_chat_messages(room, question), room.owner):
                text = item.get("text") or ""
                if not text:
                    continue
                if item.get("type") == "reasoning":
                    delta = reasoning_masker.feed(text)
                    if delta:
                        reasoning_chunks.append(delta)
                        yield {"event": "reasoning", "data": {"delta": delta}}
                else:
                    delta = content_masker.feed(text)
                    if delta:
                        chunks.append(delta)
                        yield {"event": "delta", "data": {"delta": delta}}
    except DjangoValidationError as exc:
        detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
        if not chunks and not reasoning_chunks:
            fallback, __ = chat_service.create_message(
                room, None, detail, message_type=ChatMessage.MessageType.SYSTEM, extra={"error": True, "mode": "chat"}
            )
            payload = chat_service.message_payload(fallback, room=room)
            push_room_event(room, payload)
            yield {"event": "error", "data": {"detail": detail, "message": payload}}
            return
        # 已有内容：保留部分回答；只有思考（流中断）：走下方「未给出最终回答」兜底
        if chunks:
            extra["partial"] = detail
    # 冲刷脱敏器缓冲（中断场景也要补发已缓冲的安全文本）
    content_tail = content_masker.flush()
    if content_tail:
        chunks.append(content_tail)
        yield {"event": "delta", "data": {"delta": content_tail}}
    reasoning_tail = reasoning_masker.flush()
    if reasoning_tail:
        reasoning_chunks.append(reasoning_tail)
        yield {"event": "reasoning", "data": {"delta": reasoning_tail}}
    mask_hits = content_masker.hits + reasoning_masker.hits
    if mask_hits:
        extra.setdefault("guard", {})["mask_hits"] = mask_hits
    if reasoning_chunks:
        extra["reasoning"] = _clip_reasoning("".join(reasoning_chunks))
    if not chunks:
        # 只有思考没有回答（思考过长被截断 / 思考中流中断）：思考保留在 extra，
        # 内容用可读文案兜底（消息内容不允许为空）
        extra["no_answer"] = True
        chunks.append(str(_("The model did not provide a final answer; please retry or switch models")))
    reply, __ = chat_service.create_message(
        room, None, "".join(chunks), message_type=ChatMessage.MessageType.AI, extra=extra
    )
    payload = chat_service.message_payload(reply, room=room)
    push_room_event(room, payload)
    yield {"event": "done", "data": {"mode": extra.get("mode", "chat"), "message": payload}}


def markdown_hint() -> str:
    """命令提示文案（前端占位/帮助，供 i18n 与文档共用）。

    文案不含尖括号占位符：该串会直接渲染到前端（含 i18n 场景），尖括号易被误当标签。
    """
    return str(
        _(
            "Type /kb followed by a question to ask the knowledge base, or /do to request an "
            "action draft (confirm before execution)"
        )
    )


def ai_gate_error() -> str:
    """AI 门禁的可读提示（未启用/未配置）。"""
    if not settings.AI_ASSISTANT_ENABLED:
        return str(_("AI assistant is not enabled"))
    return str(_("AI assistant is not configured"))
