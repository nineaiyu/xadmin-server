#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库文档管理：仓库扫描/分块入库 + 上传文档维护（自 ai.utils.ai 拆分，行为不变）。

安全口径：知识库文档两类来源——仓库文件（docs/**/*.md + 根 README/CONTRIBUTING，
命令同步）与管理端上传（存 DB 全文）；检索链路不查询任何业务模型（不触生产数据）。
"""

import hashlib
import re
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ai.utils.index_meta import invalidate_index_meta
from common.utils import get_logger

logger = get_logger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
# 知识库来源：docs/ 全部 markdown + 根目录关键文档
DOCS_DIR = PROJECT_DIR / "docs"
ROOT_DOCS = ["README.md", "CONTRIBUTING.md"]
CHUNK_WINDOW = 1200  # 长块滑动窗口字符数
# 上传文档：名称与全文上限（知识库为文本资产，DB 存储，200KB 文本已覆盖手册级文档）
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
    """按文档全文重建其全部分块（先删后插），返回块数。

    向量保留：正文未变的块（``content_hash`` 相同）沿用既有 embedding——重建会
    重新分配块主键，若不保留则每次仓库同步/重传都会让向量全部失效，重算即
    embedding API 成本；陈旧块（正文变更）不带向量，由显式构建补齐。
    """
    from ai.models.ai import AiKnowledgeChunk

    preserved: dict[str, tuple] = {}
    existing = AiKnowledgeChunk.objects.filter(source_path=doc.path).exclude(embedding__isnull=True)
    for content_hash, embedding, embedding_model, embedding_hash, embedding_dim in existing.values_list(
        "content_hash", "embedding", "embedding_model", "embedding_hash", "embedding_dim"
    ):
        preserved.setdefault(content_hash, (embedding, embedding_model, embedding_hash, embedding_dim))
    AiKnowledgeChunk.objects.filter(source_path=doc.path).delete()
    chunks = _chunk_markdown(doc.content or "")
    rows = []
    for index, chunk in enumerate(chunks):
        content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        vector = preserved.get(content_hash)
        rows.append(
            AiKnowledgeChunk(
                source_path=doc.path,
                title=(doc.title or doc.path)[:255],
                chunk_index=index,
                content=chunk,
                content_hash=content_hash,
                **(
                    {
                        "embedding": vector[0],
                        "embedding_model": vector[1],
                        "embedding_hash": vector[2],
                        "embedding_dim": vector[3],
                    }
                    if vector
                    else {}
                ),
            )
        )
    AiKnowledgeChunk.objects.bulk_create(rows)
    # 块集合变化：清索引元数据签名缓存（本进程立即生效，多 worker 由短 TTL 兜底）
    invalidate_index_meta()
    return len(chunks)


def remove_chunks(path: str) -> None:
    """移除某文档的全部分块（停用/删除时调用，检索索引即块集合）。"""
    from ai.models.ai import AiKnowledgeChunk

    AiKnowledgeChunk.objects.filter(source_path=path).delete()
    invalidate_index_meta()


def upsert_upload_document(name: str, content: str, creator=None):
    """创建/覆盖上传文档并重建分块，返回 (doc, created)。

    同名（稳定 path）视为更新——「重新上传即覆盖」，列表不会出现同名多份；
    路径前缀 upload/ 与仓库文档隔离，sync 不参与其维护。
    """
    from ai.models.ai import AiKnowledgeDocument, upload_document_path

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
    # 正文变更自动补齐向量（ADR-082）：文档行已落库后调度，无档案/无待建静默跳过
    from ai.utils.ai_embeddings import schedule_auto_rebuild

    schedule_auto_rebuild(doc)
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
    # 启用路径重建的分块全为待建（停用期分块已清、向量保留图失效），自动补齐（ADR-082）；
    # 停用路径无块可建，调度入口按「无待建块」静默跳过
    from ai.utils.ai_embeddings import schedule_auto_rebuild

    schedule_auto_rebuild(doc)


def sync_knowledge() -> dict:
    """扫描仓库文档 → 登记/分块入库（内容 hash 幂等）；返回同步摘要。

    只维护 repo 来源（双来源边界）：上传文档（source_type=upload 与
    upload/ 前缀分块）不参与扫描与清理；仓库文件消失、或历史遗留的孤儿块
    （无文档登记的 repo 块）会被清理。本批有变更且向量链路可用时调度一次
    全量增量向量构建（摘要 ``vector_rebuild`` 标记，ADR-082）。
    """
    from ai.models.ai import UPLOAD_PATH_PREFIX, AiKnowledgeChunk, AiKnowledgeDocument

    created = updated = removed = rebuilt = 0
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
        rebuilt += 1
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
    # 仓库同步可能跨多文档变更：只调度一次全量增量构建（一次任务吃掉全部陈旧块，
    # 不逐文档排队）；无档案 / 无待建 / 构建中由调度入口静默跳过（ADR-082）
    from ai.utils.ai_embeddings import schedule_auto_rebuild

    vector_rebuild = schedule_auto_rebuild(None) if rebuilt else False
    summary = {
        "created": created,
        "updated": updated,
        "removed": removed,
        "total": AiKnowledgeChunk.objects.count(),
        "vector_rebuild": vector_rebuild,
        "synced_at": timezone.now().isoformat(),
    }
    logger.info("AI knowledge sync: %s", summary)
    return summary
