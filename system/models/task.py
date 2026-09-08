#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : task
# author : ly_13
# date : 9/8/2026
"""定时任务执行记录。

所有 celery 任务（定时调度 + 手动执行）的统一执行历史：
- 主键 id 即 celery task_id，投递前预创建，天然与 celery 日志文件/结果对齐；
- 状态流转 PENDING → RUNNING → SUCCESS/FAILURE/REVOKED，由 celery 信号自动推进
  （system/signal_task_execution.py），业务任务代码零侵入；
- creator 经全局 pre_save 信号自动记录（common/signal_handlers.py），
  定时调度无请求上下文，creator 为空即系统调度。
"""
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from common.core.models import DbAuditModel


class TaskExecution(DbAuditModel):
    """一次 celery 任务投递的执行记录（pk == celery task_id）。"""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        RUNNING = "RUNNING", _("Running")
        SUCCESS = "SUCCESS", _("Success")
        FAILURE = "FAILURE", _("Failure")
        REVOKED = "REVOKED", _("Revoked")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Task Name"), max_length=255, db_index=True)
    periodic_task = models.ForeignKey(
        PeriodicTask, verbose_name=_("Periodic Task"), on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    args = models.JSONField(_("Positional Args"), default=list, blank=True)
    kwargs = models.JSONField(_("Keyword Args"), default=dict, blank=True)
    status = models.CharField(
        _("Status"), max_length=16, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )
    date_start = models.DateTimeField(_("Start Time"), null=True, blank=True)
    date_finished = models.DateTimeField(_("Finish Time"), null=True, blank=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Task Execution")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}({self.pk})"

    @property
    def time_cost(self):
        """执行耗时（秒），未开始或未结束返回 None。"""
        if self.date_start and self.date_finished:
            return (self.date_finished - self.date_start).total_seconds()
        return None
