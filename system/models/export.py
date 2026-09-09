#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导出记录（下载中心）。

一次异步导出对应一条记录，主键 id 即 celery task_id，与 TaskExecution 同 pk：
- 任务投递后 after_task_publish 信号自动建同 pk 的 TaskExecution，因此执行历史页
  与增量日志（CELERY_LOG_DIR/<task_id>.log）零成本复用；
- 任务内直接用 logger 写进度即可，前端复用 TaskLogDialog 消费。

产物落 UploadFile(is_tmp=True)，复用临时文件定时清理能力；记录本身由
auto_clean_export_record_job 按 EXPORT_FILE_KEEP_DAYS（默认 7 天）清理。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class ExportRecord(DbAuditModel):
    """异步导出记录（pk == celery task_id，下载中心列表数据源）。"""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        RUNNING = "RUNNING", _("Running")
        SUCCESS = "SUCCESS", _("Success")
        FAILURE = "FAILURE", _("Failure")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("File name"), max_length=255)
    module = models.CharField(_("Source module"), max_length=128, blank=True, null=True)
    path = models.CharField(_("Source path"), max_length=255, blank=True, null=True)
    file_format = models.CharField(_("File format"), max_length=16, default="xlsx", db_index=True)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    rows = models.IntegerField(_("Row count"), null=True, blank=True)
    # 运行中分批上报（0-100），终态 SUCCESS 置 100；导出为整体渲染，仅里程碑粒度
    progress = models.PositiveSmallIntegerField(_("Progress"), default=0)
    file = models.ForeignKey(
        "system.UploadFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Export file"),
    )
    error = models.TextField(_("Error message"), null=True, blank=True)
    params = models.JSONField(_("Export params"), default=dict, blank=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Export record")
        indexes = [models.Index(fields=["status", "created_time"], name="idx_export_status_created")]

    def __str__(self):
        return f"{self.name}({self.pk})"

    @property
    def filesize(self):
        """产物字节数，未生成或文件缺失时返回 None。"""
        return getattr(self.file, "filesize", None) if self.file_id else None
