#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手核心工具：问答链路（知识库文档管理拆至 ``ai_knowledge``，此处再导出）。

安全口径：
- 知识库文档两类来源：仓库文件（docs/**/*.md + 根 README/CONTRIBUTING，命令同步）
  与管理端上传（存 DB 全文）；ask 链路不查询任何业务模型（不触生产数据）；
- 检索基线为零依赖词频重叠评分（CJK 二元组 + ASCII 词 + 标题加成）；
  配置 ``purpose=embedding`` 激活档案并构建向量后走 RRF 混合检索
  （``ai/utils/ai_embeddings.py``），未配置 = 零变化；
- LLM 配置经 Setting 值级加密（AI_API_KEY write_only），未启用/未配置统一
  可读降级。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from ai.utils.ai_config import (  # noqa: F401 配置/凭据拆至 ai_config（行数门禁），此处再导出保持调用面
    BUILTIN_PERSONA,
    PURPOSE_CHAT,
    PURPOSE_EMBEDDING,
    PURPOSE_STRUCTURED,
    STRUCTURED_MAX_TOKENS,
    active_profile,
    active_profile_name,
    ai_context_limit,
    ai_credentials,
    ai_persona,
    ai_structured_max_tokens,
    embedding_credentials,
    embedding_enabled,
    embedding_profile,
    is_configured,
    is_enabled,
    native_tools_enabled,
    profile_credentials,
    profile_for,
    set_active_profile,
    structured_chat_client,
)
from ai.utils.ai_knowledge import (  # noqa: F401  (知识库文档管理拆至 ai_knowledge，此处再导出保持调用面)
    CHUNK_WINDOW,
    DOCS_DIR,
    MAX_UPLOAD_CONTENT_LENGTH,
    MAX_UPLOAD_NAME_LENGTH,
    PROJECT_DIR,
    ROOT_DOCS,
    _chunk_markdown,
    _doc_title,
    _iter_doc_files,
    rebuild_chunks,
    remove_chunks,
    set_document_active,
    sync_knowledge,
    upsert_upload_document,
)
from ai.utils.ai_retrieval import (  # noqa: F401 检索链路拆至 ai_retrieval（含块级分词缓存），此处再导出保持调用面
    MAX_QUESTION_LENGTH,
    SCORE_THRESHOLD,
    TOP_K,
    _tokenize,
    retrieve,
)


def _prepare_rag(question: str, user=None) -> tuple:
    """问答链路公共部分：问题校验 + 检索 + prompt 构造（引用数据块 + 注入标记）。

    返回 ``(messages, sources, injection_hits)``；问题为空/未启用/无命中抛可读
    ValidationError。检索片段以引用数据块包裹（护栏），命中可疑指令模式时
    打标 + 落 AI:security 告警（不阻断，避免误杀）。
    """
    from ai.utils.ai_guard import REFERENCE_GUARD_INSTRUCTION, annotate_reference

    question = (question or "").strip()[:MAX_QUESTION_LENGTH]
    if not question:
        raise DjangoValidationError(_("Question cannot be empty"))
    if not is_enabled():
        raise DjangoValidationError(_("AI assistant is not enabled or configured"))

    retrieved = retrieve(question)
    if not retrieved:
        raise DjangoValidationError(_("No matching documents found for this question"))

    context_blocks = []
    sources = []
    injection_hits: list = []
    for index, item in enumerate(retrieved, start=1):
        chunk = item["chunk"]
        block, hits = annotate_reference(
            chunk.content,
            label=f"[{index}] {chunk.title} ({chunk.source_path})",
            user=user,
            kind="knowledge",
        )
        for name in hits:
            if name not in injection_hits:
                injection_hits.append(name)
        context_blocks.append(block)
        sources.append({"title": chunk.title, "path": chunk.source_path, "chunk_index": chunk.chunk_index})

    system_prompt = "{} {}".format(
        str(
            _(
                "You are the xadmin usage/development assistant. Answer ONLY based on the "
                "provided reference documents, cite them as [n] markers, and say you don't "
                "know when the documents do not cover the question."
            )
        ),
        str(REFERENCE_GUARD_INSTRUCTION),
    )
    user_prompt = "{}\n\n---\n{}".format(
        "\n\n".join(context_blocks),
        str(_("Question: {}").format(question)),
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return messages, sources, injection_hits


def readable_ai_error(exc) -> str:
    """LLM 调用失败 → 展示可读文案（i18n）。

    SDK 内部英文错误按已知类别归一（思考型模型的「只思考未回答」与「空回答」
    分开提示，便于用户采取「重试 / 更换模型」动作），其余归服务暂时不可用。
    """
    text = str(exc or "")
    if "concurrent" in text.lower():
        return str(_("Too many AI requests are running; please retry in a moment"))
    if "only reasoning content" in text or "did not provide a final answer" in text:
        return str(_("The model did not provide a final answer; please retry or switch models"))
    if "empty answer" in text:
        return str(_("The model returned an empty answer; please retry or switch models"))
    return str(_("AI service is temporarily unavailable"))


def prepare_ask(question: str, user=None) -> tuple:
    """流式端点「响应头发出前」的同步预检 + 上下文装配：返回 (messages, sources)。

    与 ask / ask_stream 校验完全同源（空问题 / 未启用 / 无命中 → 可读 ValidationError）。
    """
    messages, sources, __hits = _prepare_rag(question, user=user)
    return messages, sources


def ask(question: str, user=None) -> dict:
    """问答链路（非流式）：检索 → LLM → 可读答案 + 出处。异常转可读 ValidationError 语义。

    输出文本过安全护栏（敏感形态 + 规则形态脱敏），命中计数与 prompt 摘要进 ``_guard``
    供调用方写审计；返回契约中的 answer/sources 不变，调用方负责剥离下划线键。
    """
    from ai.utils.ai_guard import guard_summary, mask_text
    from ai.utils.ai_usage import tracked_chat
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient

    messages, sources, injection_hits = _prepare_rag(question, user=user)
    try:
        client = ChatCompletionsClient(ai_credentials())
        answer = tracked_chat(user, "docs", messages, client=client)
    except AiSdkError as exc:
        raise DjangoValidationError(readable_ai_error(exc)) from exc
    answer, mask_hits = mask_text(answer, user)
    # _usage 供调用方写审计（成本维度观测）；_guard 为护栏摘要（prompt 摘要/注入/脱敏）
    return {
        "answer": answer,
        "sources": sources,
        "_usage": getattr(client, "last_usage", None),
        "_guard": guard_summary(
            prompt=question,
            injection=injection_hits,
            mask_hits=mask_hits,
            output_len=len(answer or ""),
        ),
    }


def ask_stream(messages: list, sources: list, user=None):
    """问答链路（流式生成器）：产出事件 dict，供 SSE 转发。

    增量事件：``{"type": "reasoning"|"content", "text": ...}``（思考型模型有 reasoning）；
    流末尾产出 ``{"type": "done", "answer": ..., "sources": [...], "guard": {...}}``。

    messages/sources 由 ``prepare_ask`` 装配——视图层先做同步校验，保持
    「校验错误在响应头前返回 JSON 1001」契约，同时不阻塞首包（模型思考再久，
    响应头也已发出、前端可先渲染「思考中」）。流内失败抛可读 ValidationError。

    护栏：正文与思考增量均经 ``StreamMasker`` 逐段脱敏（hold-back 防跨帧敏感串
    泄漏），done 的 answer 为脱敏后全文（落库与前端展示同源）。
    """
    from ai.utils.ai_guard import StreamMasker, guard_summary
    from ai.utils.ai_usage import tracked_chat_stream
    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient

    client = ChatCompletionsClient(ai_credentials())
    content_masker = StreamMasker(user)
    reasoning_masker = StreamMasker(user)
    chunks = []
    try:
        for item in tracked_chat_stream(user, "docs", client, messages):
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
