#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手知识库（ADR-023 / ADR-033）：文档登记 + 分块入库。不触生产数据。

两类来源：
- repo：仓库 docs/**/*.md + 根 README/CONTRIBUTING，由 sync_ai_knowledge 命令扫描维护；
- upload：管理端「知识库」页上传/编辑的文档，存全文于 DB（不落文件系统）。

检索面仍是 AiKnowledgeChunk（source_path 关联文档路径，upload 前缀 upload/ 区分来源）；
文档停用/删除即移除其分块（检索索引 = 有块的文档集合），无需改检索链路。
"""

import hashlib

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

# 上传文档的存储路径前缀（与仓库文档路径隔离，同步清理时不越界）
UPLOAD_PATH_PREFIX = "upload/"


def upload_document_path(name: str) -> str:
    """上传文档的稳定存储路径：同一名称重复上传落到同一 path（覆盖更新语义）。"""
    return f"{UPLOAD_PATH_PREFIX}{name}.md"


class AiKnowledgeDocument(DbAuditModel, DbUuidModel):
    """知识库文档（ADR-033）：管理端上传 + 仓库同步统一登记，内容存 DB 供预览。"""

    class SourceType(models.TextChoices):
        REPO = "repo", _("Repository docs")
        UPLOAD = "upload", _("Uploaded document")

    source_type = models.CharField(
        _("Source type"), max_length=16, choices=SourceType.choices, default=SourceType.REPO, db_index=True
    )
    path = models.CharField(_("Storage path"), max_length=255, unique=True)
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    content = models.TextField(_("Content"), blank=True, default="")
    content_hash = models.CharField(_("Content hash"), max_length=64, blank=True, default="")
    chunk_count = models.IntegerField(_("Chunk count"), default=0)
    is_active = models.BooleanField(_("Active"), default=True, db_index=True)
    synced_at = models.DateTimeField(_("Synced at"), auto_now=True)

    class Meta:
        verbose_name = _("AI knowledge document")
        verbose_name_plural = _("AI knowledge documents")
        ordering = ("-synced_at",)

    def __str__(self):
        return self.path

    @property
    def is_upload(self) -> bool:
        return self.source_type == self.SourceType.UPLOAD

    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


class AiKnowledgeChunk(DbAuditModel, DbUuidModel):
    """文档知识块：按 ## 边界切分（长块滑动窗口），检索的最小单元。"""

    source_path = models.CharField(_("Source path"), max_length=255, db_index=True)
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    chunk_index = models.IntegerField(_("Chunk index"), default=0)
    content = models.TextField(_("Content"))
    content_hash = models.CharField(_("Content hash"), max_length=64)
    synced_at = models.DateTimeField(_("Synced at"), auto_now=True)

    class Meta:
        verbose_name = _("AI knowledge chunk")
        verbose_name_plural = _("AI knowledge chunks")
        ordering = ("source_path", "chunk_index")
        constraints = [models.UniqueConstraint(fields=["source_path", "chunk_index"], name="uniq_ai_chunk_path_index")]

    def __str__(self):
        return f"{self.source_path}#{self.chunk_index}"
