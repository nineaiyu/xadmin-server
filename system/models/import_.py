#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导入记录。

一次异步导入对应一条记录，主键 id 即 celery task_id，与 TaskExecution 同 pk
（对齐 ExportRecord 范式）：
- 任务投递后 after_task_publish 信号自动建同 pk 的 TaskExecution；同步执行
  （apply/EAGER）不发该信号，任务内补建，执行历史页与增量日志两种环境都可用；
- 源文件与失败行错误报告均落 UploadFile(is_tmp=True)，复用临时文件定时清理；
- 记录本身由 auto_clean_import_record_job 按 IMPORT_RECORD_KEEP_DAYS（默认 30 天）清理。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class ImportRecord(DbAuditModel):
    """异步导入记录（pk == celery task_id，下载中心「导入记录」页签数据源）。"""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        RUNNING = "RUNNING", _("Running")
        SUCCESS = "SUCCESS", _("Success")
        FAILURE = "FAILURE", _("Failure")

    class Action(models.TextChoices):
        CREATE = "create", _("Create")
        UPDATE = "update", _("Update")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("File name"), max_length=255)
    module = models.CharField(_("Target module"), max_length=128, blank=True, null=True)
    path = models.CharField(_("Source path"), max_length=255, blank=True, null=True)
    action = models.CharField(
        _("Import action"), max_length=16, choices=Action.choices, default=Action.CREATE, db_index=True
    )
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    total = models.IntegerField(_("Total rows"), null=True, blank=True)
    success_rows = models.IntegerField(_("Success rows"), default=0)
    failed_rows = models.IntegerField(_("Failed rows"), default=0)
    # 运行中按已处理行数分批上报（0-100），终态 SUCCESS 置 100；运行中同步刷新行数统计
    progress = models.PositiveSmallIntegerField(_("Progress"), default=0)
    source_file = models.ForeignKey(
        "system.UploadFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_sources",
        verbose_name=_("Source file"),
    )
    error_report = models.ForeignKey(
        "system.UploadFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_error_reports",
        verbose_name=_("Error report"),
    )
    error = models.TextField(_("Error message"), null=True, blank=True)
    params = models.JSONField(_("Import params"), default=dict, blank=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Import record")
        indexes = [models.Index(fields=["status", "created_time"], name="idx_import_status_created")]

    def __str__(self):
        return f"{self.name}({self.pk})"

    @property
    def report_filesize(self):
        """错误报告字节数，未生成或文件缺失时返回 None。"""
        return getattr(self.error_report, "filesize", None) if self.error_report_id else None


class ImportTemplate(DbUuidModel, DbAuditModel):
    """导入列映射模板：把「原始表头 → 目标字段」的映射持久化复用。

    - 按目标模型（label_lower）隔离，避免跨模型误用；
    - 个人模板（creator 可见）+ 全局共享模板（is_shared，仅超管可建改）两档；
    - 映射为显式字典，不做模糊/语义推断（见 common/core/import_mapping.py）。
    """

    model = models.CharField(
        _("Target model"), max_length=128, db_index=True, help_text=_("Model label like system.userinfo")
    )
    name = models.CharField(_("Template name"), max_length=64)
    mapping = models.JSONField(_("Column mapping"), default=dict, blank=True)
    options = models.JSONField(_("Import options"), default=dict, blank=True)
    is_shared = models.BooleanField(_("Shared"), default=False, db_index=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Import template")
        indexes = [models.Index(fields=["model", "is_shared"], name="idx_import_tpl_model_shared")]
        constraints = [
            # 同一模型下同名模板唯一（按创建人隔离）。creator 为可空外键，
            # 唯一性由 serializer.validate() 显式查重，DB 约束兜底并发
            models.UniqueConstraint(
                fields=["model", "name", "creator"], name="uniq_import_template_model_name_creator"
            ),
        ]

    def __str__(self):
        return f"{self.name}({self.model})"
