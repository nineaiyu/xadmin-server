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
  （task/signal_task_execution.py），业务任务代码零侵入；
- creator 经全局 pre_save 信号自动记录（common/signal_handlers.py）：手动执行
  为触发者；定时派发无请求上下文，回溯到所属周期任务的配置者
  （PeriodicTaskOwner side 表），未登记归属（系统注册/种子/存量任务）保持
  为空即系统调度。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from common.core.models import DbAuditModel


class CeleryTaskRecordModel(DbAuditModel):
    """pk 即 celery task_id 的异步任务记录基类（显式化共享主键命名空间契约）。

    TaskExecution 与 ExportRecord 共用同一主键取值：记录在任务投递前预创建
    （pk = task_id），after_task_publish 信号自动补建同 pk 的 TaskExecution——
    日志文件（CELERY_LOG_DIR/<task_id>.log）与结果因此按 task_id 零成本对齐。
    跨表按 pk 定位记录（如 task/ws.py 的日志归属判定）依赖此契约；
    新增承载 celery 任务的记录模型应继承本基类以纳入约定。
    """

    class Meta:
        abstract = True


class TaskExecution(CeleryTaskRecordModel):
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
        PeriodicTask,
        verbose_name=_("Periodic Task"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    args = models.JSONField(_("Positional Args"), default=list, blank=True)
    kwargs = models.JSONField(_("Keyword Args"), default=dict, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
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


class PeriodicTaskOwner(DbAuditModel):
    """周期任务配置者归属（side 表）：与 django_celery_beat.PeriodicTask 一对一。

    PeriodicTask 是第三方调度模型，无审计字段，表达不了「这条定时配置是谁配的」；
    定时派发产生的 TaskExecution 因此拿不到 creator——任务中心对非超管按 creator
    圈数据域，归属缺失的行普通用户不可见也不可取消。本表本地补齐配置者归属：

    - 落行时机：PeriodicTask 创建时统一由 task/signal_handler.py 的 post_save
      落行（record_for 幂等补行，绝不覆盖既有归属）——创建入口分散在页面
      创建/克隆、启动期系统注册、种子与 Admin，显式写入难收敛且新入口易漏；
    - creator 依赖全局审计信号回填：页面创建/克隆为操作者；beat/worker/命令行
      等无请求上下文（系统注册、种子）即为空，即「系统注册，无人工归属」；
    - 更新配置不改归属（含 beat 启停簿记），克隆视为新建、归属克隆操作者；
    - 任务删除（页面删除/孤儿清理/调度级联删除）随 OneToOne CASCADE 一并清除。
    """

    periodic_task = models.OneToOneField(
        PeriodicTask,
        verbose_name=_("Periodic Task"),
        on_delete=models.CASCADE,
        related_name="config_owner",
    )

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Periodic task owner")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.periodic_task_id}->{self.creator_id}"

    @classmethod
    def record_for(cls, periodic_task, creator=None):
        """为周期任务落归属记录（幂等）：已有归属不覆盖。

        信号重放、并发落行、种子重跑等场景重复调用时保留首个归属——归属是
        「谁配置的」这一事实，不是当前操作者的最新值。
        """
        owner, _created = cls.objects.get_or_create(periodic_task=periodic_task, defaults={"creator": creator})
        return owner
