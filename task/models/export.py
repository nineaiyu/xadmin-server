#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导出记录（下载中心）。

一次异步导出对应一条记录，主键 id 即 celery task_id，与 TaskExecution 同 pk：
- 任务投递后 after_task_publish 信号自动建同 pk 的 TaskExecution，因此执行历史页
  与增量日志（CELERY_LOG_DIR/<task_id>.log）零成本复用；
- 任务内直接用 logger 写进度即可，前端复用 TaskLogDialog 消费。

幂等性：``id`` 既是主键又是 task_id，**同一任务落两行在库层面不可能**；重复投递
（broker 重投、手工重放）只会重跑同一个 pk，由 ``run_async_export`` 的终态守卫
（``TERMINAL_STATUSES``）拦下，不再重复渲染与产物落盘。「任务中心 → 重跑」按语义
是**新任务**（新 pk + 记录名带 ``-rerun`` 后缀），不属重复投递。

产物落 UploadFile(is_tmp=True)，复用临时文件定时清理能力；记录本身由
auto_clean_export_record_job 按 EXPORT_FILE_KEEP_DAYS（默认 7 天）清理。
"""

import uuid
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from .task import CeleryTaskRecordModel


class ExportRecord(CeleryTaskRecordModel):
    """异步导出记录（pk == celery task_id，下载中心列表数据源）。"""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        RUNNING = "RUNNING", _("Running")
        SUCCESS = "SUCCESS", _("Success")
        FAILURE = "FAILURE", _("Failure")
        # 用户取消（任务中心）：与失败区分，便于运营判断「是坏了还是停了」
        REVOKED = "REVOKED", _("Cancelled")

    # 终态集合：任务重投递 / 重放时的幂等守卫（见 task/services/_export.run_async_export）
    TERMINAL_STATUSES = (Status.SUCCESS, Status.FAILURE, Status.REVOKED)

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
    # 统一进度助手写入的阶段描述（如「统计行数 / 渲染内容」）
    stage = models.CharField(_("Progress stage"), max_length=64, blank=True, default="")
    file = models.ForeignKey(
        "file.UploadFile",
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

    def __str__(self) -> str:
        return f"{self.name}({self.pk})"

    @property
    def filesize(self) -> Any:
        """产物字节数，未生成或文件缺失时返回 None。"""
        return getattr(self.file, "filesize", None) if self.file_id else None
