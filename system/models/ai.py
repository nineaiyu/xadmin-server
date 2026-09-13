#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手知识库（ADR-023）：仓库文档分块入库。不触生产数据。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class AiKnowledgeChunk(DbAuditModel, DbUuidModel):
    """文档知识块：docs/ markdown 按 ## 边界切分（长块滑动窗口）。"""

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
