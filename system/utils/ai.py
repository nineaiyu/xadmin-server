#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手核心工具（ADR-023 / ADR-033）：知识库同步 + 词频检索 + 问答链路。

安全口径：
- 知识库文档两类来源：仓库文件（docs/**/*.md + 根 README/CONTRIBUTING，命令同步）
  与管理端上传（存 DB 全文，ADR-033）；ask 链路不查询任何业务模型（不触生产数据）；
- 检索为零依赖词频重叠评分（CJK 二元组 + ASCII 词 + 标题加成），
  向量嵌入升级路径登记候选池；
- LLM 配置经 Setting 值级加密（AI_API_KEY write_only），未启用/未配置统一
  可读降级。
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
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
# 上传文档（ADR-033）：名称与全文上限（知识库为文本资产，DB 存储，200KB 文本已覆盖手册级文档）
MAX_UPLOAD_NAME_LENGTH = 120
MAX_UPLOAD_CONTENT_LENGTH = 200_000


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


def rebuild_chunks(doc) -> int:
    """按文档全文重建其全部分块（先删后插），返回块数。"""
    from system.models.ai import AiKnowledgeChunk

    AiKnowledgeChunk.objects.filter(source_path=doc.path).delete()
    chunks = _chunk_markdown(doc.content or "")
    AiKnowledgeChunk.objects.bulk_create(
        [
            AiKnowledgeChunk(
                source_path=doc.path,
                title=(doc.title or doc.path)[:255],
                chunk_index=index,
                content=chunk,
                content_hash=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
            )
            for index, chunk in enumerate(chunks)
        ]
    )
    return len(chunks)


def remove_chunks(path: str) -> None:
    """移除某文档的全部分块（停用/删除时调用，检索索引即块集合）。"""
    from system.models.ai import AiKnowledgeChunk

    AiKnowledgeChunk.objects.filter(source_path=path).delete()


def upsert_upload_document(name: str, content: str, creator=None):
    """创建/覆盖上传文档并重建分块，返回 (doc, created)。

    同名（稳定 path）视为更新——「重新上传即覆盖」，列表不会出现同名多份；
    路径前缀 upload/ 与仓库文档隔离，sync 不参与其维护（ADR-033）。
    """
    from system.models.ai import AiKnowledgeDocument, upload_document_path

    path = upload_document_path(name)
    doc = AiKnowledgeDocument.objects.filter(path=path).first()
    created = doc is None
    if doc is None:
        doc = AiKnowledgeDocument(path=path, source_type=AiKnowledgeDocument.SourceType.UPLOAD)
    elif doc.source_type != AiKnowledgeDocument.SourceType.UPLOAD:
        raise DjangoValidationError(_("Path {} conflicts with a repository document").format(path))
    doc.title = name[:255]
    doc.content = content
    doc.content_hash = AiKnowledgeDocument.hash_content(content)
    doc.is_active = True
    doc.chunk_count = rebuild_chunks(doc)
    if creator is not None and getattr(creator, "pk", None):
        doc.modifier = creator
        if created:
            doc.creator = creator
    doc.save()
    return doc, created


def set_document_active(doc, active: bool) -> None:
    """停用 = 移除分块（不参与检索，内容保留可再启用）；启用 = 重建分块。"""
    if active:
        doc.chunk_count = rebuild_chunks(doc)
    else:
        remove_chunks(doc.path)
        doc.chunk_count = 0
    doc.is_active = bool(active)
    doc.save(update_fields=["is_active", "chunk_count", "synced_at"])


def sync_knowledge() -> dict:
    """扫描仓库文档 → 登记/分块入库（内容 hash 幂等）；返回同步摘要。

    只维护 repo 来源（ADR-033 双来源边界）：上传文档（source_type=upload 与
    upload/ 前缀分块）不参与扫描与清理；仓库文件消失、或历史遗留的孤儿块
    （无文档登记的 repo 块）会被清理。
    """
    from system.models.ai import UPLOAD_PATH_PREFIX, AiKnowledgeChunk, AiKnowledgeDocument

    created = updated = removed = 0
    seen_paths = set()
    for path, rel_path in _iter_doc_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        seen_paths.add(rel_path)
        title = _doc_title(text, path.stem)
        content_hash = AiKnowledgeDocument.hash_content(text)
        doc = AiKnowledgeDocument.objects.filter(path=rel_path).first()
        if doc is not None and doc.source_type != AiKnowledgeDocument.SourceType.REPO:
            # 仓库文件与上传文档路径撞名（理论不可达：upload/ 前缀隔离）：跳过不覆盖
            logger.warning("ai knowledge path conflict with upload document: %s", rel_path)
            continue
        if doc is not None and doc.content_hash == content_hash:
            # hash 快路径；块被外部清理时自愈重建（少见，一次 count 的代价）
            if doc.chunk_count == AiKnowledgeChunk.objects.filter(source_path=rel_path).count():
                continue
        else:
            if doc is None:
                doc = AiKnowledgeDocument(path=rel_path, source_type=AiKnowledgeDocument.SourceType.REPO)
                created += 1
            else:
                updated += 1
        doc.title = title
        doc.content = text
        doc.content_hash = content_hash
        doc.is_active = True
        doc.chunk_count = rebuild_chunks(doc)
        doc.save()
    # 清理已消失的仓库文档（含分块）；upload 来源与 upload/ 前缀分块不动
    stale_paths = list(
        AiKnowledgeDocument.objects.filter(source_type=AiKnowledgeDocument.SourceType.REPO)
        .exclude(path__in=seen_paths)
        .values_list("path", flat=True)
    )
    if stale_paths:
        AiKnowledgeChunk.objects.filter(source_path__in=stale_paths).delete()
        removed += AiKnowledgeDocument.objects.filter(path__in=stale_paths).delete()[0]
    # 孤儿块：无文档登记的 repo 前缀块（旧版遗留/手工造），一并清理
    orphans = AiKnowledgeChunk.objects.exclude(source_path__startswith=UPLOAD_PATH_PREFIX).exclude(
        source_path__in=seen_paths
    )
    removed += orphans.count()
    orphans.delete()
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
