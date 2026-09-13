#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 AI 助手（ADR-034）：通用多轮对话 + `/kb` 知识库问答双形态。

- 通用多轮：读取本会话最近 N 条消息裁剪上下文 + 内置助手人设 → ChatCompletionsClient；
- `/kb 问题`：复用 system.utils.ai 的知识库 RAG（ask）链路，回复附引用来源（extra.sources）；
- 门禁：`AI_ASSISTANT_ENABLED` 且凭据齐全才可用（未启用/未配置统一可读降级，不静默）；
- 异常降级：LLM 失败落一条 system 消息（前端可见），REST 同时返回可读 detail；
- 会话走 REST 同步（LLM 5~60s，不占用 WS 长连接）；流式 SSE 留二期。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message import chat as chat_service
from message.models import ChatMessage, ChatRoom

logger = get_logger(__name__)

# `/kb` 前缀命令：走知识库 RAG；否则通用多轮对话
KB_COMMAND = "/kb"
# 内置助手人设（一期写死默认值，配置化留后续）
DEFAULT_PERSONA = (
    "You are the xadmin in-app assistant. Answer concisely and accurately in the user's language. "
    "If you are unsure, say so instead of making things up."
)


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


def history_messages(room: ChatRoom, limit: int = chat_service.AI_CONTEXT_LIMIT, drop_last_user: bool = False) -> list:
    """取会话最近 N 条消息（时间正序）：用户消息 → user，AI 回复 → assistant，系统消息跳过。

    drop_last_user：本轮提问已先落库，裁剪上下文时去掉末条 user 消息，避免重复一轮。
    """
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
    return (
        [{"role": "system", "content": DEFAULT_PERSONA}]
        + history_messages(room, drop_last_user=True)
        + [{"role": "user", "content": question}]
    )


def _llm_reply(messages: list) -> str:
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
    from system.utils.ai import ai_credentials

    try:
        return ChatCompletionsClient(ai_credentials()).chat(messages)
    except AiSdkError as exc:
        logger.warning("chat ai llm failed: %s", exc)
        raise DjangoValidationError(_("AI service is temporarily unavailable")) from exc


def kb_answer(question: str) -> tuple:
    """知识库问答：返回 (answer, sources)；无命中/未启用转可读校验错误。"""
    from system.utils.ai import ask

    result = ask(question)
    return result["answer"], result.get("sources") or []


def ai_reply_content(room: ChatRoom, question: str) -> tuple:
    """按命令分流生成回复，返回 (content, extra, mode)。"""
    if is_kb_command(question):
        kb_question = strip_kb_command(question)
        if not kb_question:
            raise DjangoValidationError(_("Please provide a question after /kb, e.g. /kb how to reset password"))
        answer, sources = kb_answer(kb_question)
        return answer, {"mode": "kb", "sources": sources}, "kb"
    return _llm_reply(build_chat_messages(room, question)), {"mode": "chat"}, "chat"


def markdown_hint() -> str:
    """命令提示文案（前端占位/帮助，供 i18n 与文档共用）。

    文案不含尖括号占位符：该串会直接渲染到前端（含 i18n 场景），尖括号易被误当标签。
    """
    return str(_("Type /kb followed by a question to ask the knowledge base"))


def ai_gate_error() -> str:
    """AI 门禁的可读提示（未启用/未配置）。"""
    if not settings.AI_ASSISTANT_ENABLED:
        return str(_("AI assistant is not enabled"))
    return str(_("AI assistant is not configured"))
