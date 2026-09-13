#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手核心工具（ADR-023）：知识库同步 + 词频检索 + 问答链路。

安全口径：
- 知识库仅来自仓库文档文件（docs/**/*.md + 根 README/CONTRIBUTING），
  ask 链路不查询任何业务模型（不触生产数据）；
- 检索为零依赖词频重叠评分（CJK 二元组 + ASCII 词 + 标题加成），
  向量嵌入升级路径登记候选池；
- LLM 配置经 Setting 值级加密（AI_API_KEY write_only），未启用/未配置统一
  可读降级。
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
# 知识库来源：docs/ 全部 markdown + 根目录关键文档
DOCS_DIR = PROJECT_DIR / "docs"
ROOT_DOCS = ["README.md", "CONTRIBUTING.md"]
CHUNK_WINDOW = 1200  # 长块滑动窗口字符数
TOP_K = 5
SCORE_THRESHOLD = 2
MAX_QUESTION_LENGTH = 500


def _iter_doc_files() -> list:
    """yield (绝对路径, 存储相对路径)：docs/ 内相对 DOCS_DIR，根文档用文件名。"""
    result = []
    for path in DOCS_DIR.rglob("*.md"):
        if path.is_file():
            result.append((path, f"docs/{path.relative_to(DOCS_DIR).as_posix()}"))
    for name in ROOT_DOCS:
        candidate = PROJECT_DIR / name
        if candidate.is_file():
            result.append((candidate, name))
    return result


def _doc_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("#"):
            return line.lstrip("#").strip()[:255]
    return fallback[:255]


def _chunk_markdown(text: str) -> list:
    """按 ## 边界切分；超长块按 CHUNK_WINDOW 滑动窗口再切。"""
    parts = re.split(r"\n(?=##\s)", text)
    chunks = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        while len(stripped) > CHUNK_WINDOW * 1.5:
            chunks.append(stripped[:CHUNK_WINDOW])
            stripped = stripped[CHUNK_WINDOW:]
        if stripped:
            chunks.append(stripped)
    return chunks


def sync_knowledge() -> dict:
    """扫描文档 → 分块 → 按 hash 幂等入库；返回同步摘要。"""
    from system.models.ai import AiKnowledgeChunk

    created = updated = removed = 0
    seen_keys = set()
    for path, rel_path in _iter_doc_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        title = _doc_title(text, path.stem)
        for index, chunk in enumerate(_chunk_markdown(text)):
            content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            seen_keys.add((rel_path, index))
            existing = AiKnowledgeChunk.objects.filter(source_path=rel_path, chunk_index=index).first()
            if existing is None:
                AiKnowledgeChunk.objects.create(
                    source_path=rel_path,
                    title=title,
                    chunk_index=index,
                    content=chunk,
                    content_hash=content_hash,
                    synced_at=timezone.now(),
                )
                created += 1
            elif existing.content_hash != content_hash or existing.title != title:
                existing.content = chunk
                existing.content_hash = content_hash
                existing.title = title
                existing.synced_at = timezone.now()
                existing.save()
                updated += 1
    # 清理已消失的文档/块
    for chunk in AiKnowledgeChunk.objects.all():
        if (chunk.source_path, chunk.chunk_index) not in seen_keys:
            chunk.delete()
            removed += 1
    summary = {
        "created": created,
        "updated": updated,
        "removed": removed,
        "total": AiKnowledgeChunk.objects.count(),
        "synced_at": timezone.now().isoformat(),
    }
    logger.info("AI knowledge sync: %s", summary)
    return summary


def _tokenize(text: str) -> list:
    """CJK 二元组 + ASCII 词。"""
    tokens = []
    for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[a-z0-9_]+", word):
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def retrieve(question: str, top_k: int = TOP_K) -> list:
    """词频重叠评分检索，返回 [{chunk 实例, score}]（score 降序，阈值过滤）。"""
    from system.models.ai import AiKnowledgeChunk

    question = (question or "").strip()[:MAX_QUESTION_LENGTH]
    if not question:
        return []
    query_tokens = set(_tokenize(question))
    scored = []
    for chunk in AiKnowledgeChunk.objects.all().only("id", "source_path", "title", "content", "chunk_index"):
        content_tokens = _tokenize(chunk.content)
        if not content_tokens:
            continue
        freq = {}
        for token in content_tokens:
            freq[token] = freq.get(token, 0) + 1
        # 命中数（去重词元）：入阈条件；排序分再做长度归一 + 标题加成
        raw_hits = sum(1 for token in query_tokens if freq.get(token))
        title_hit = 1 if query_tokens & set(_tokenize(chunk.title)) else 0
        boosted = raw_hits + title_hit
        if boosted < SCORE_THRESHOLD:
            continue
        score = boosted / (len(content_tokens) ** 0.5)
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{"chunk": chunk, "score": round(score, 4)} for score, chunk in scored[:top_k]]


def is_configured() -> bool:
    return bool(settings.AI_BASE_URL and settings.AI_API_KEY and settings.AI_MODEL)


def is_enabled() -> bool:
    return bool(settings.AI_ASSISTANT_ENABLED) and is_configured()


def ai_credentials() -> dict:
    return {
        "base_url": settings.AI_BASE_URL,
        "api_key": settings.AI_API_KEY,
        "model": settings.AI_MODEL,
        "timeout": settings.AI_TIMEOUT,
    }


def ask(question: str) -> dict:
    """问答链路：检索 → LLM → 可读答案 + 出处。异常转可读 ValidationError 语义。"""
    from django.core.exceptions import ValidationError as DjangoValidationError

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
    for index, item in enumerate(retrieved, start=1):
        chunk = item["chunk"]
        context_blocks.append(f"[{index}] {chunk.title} ({chunk.source_path})\n{chunk.content}")
        sources.append({"title": chunk.title, "path": chunk.source_path, "chunk_index": chunk.chunk_index})

    from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient

    system_prompt = str(
        _(
            "You are the xadmin usage/development assistant. Answer ONLY based on the "
            "provided reference documents, cite them as [n] markers, and say you don't "
            "know when the documents do not cover the question."
        )
    )
    user_prompt = "{}\n\n---\n{}".format(
        "\n\n".join(context_blocks),
        str(_("Question: {}").format(question)),
    )
    try:
        client = ChatCompletionsClient(ai_credentials())
        answer = client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
    except AiSdkError as exc:
        raise DjangoValidationError(str(exc)) from exc
    return {"answer": answer, "sources": sources}
