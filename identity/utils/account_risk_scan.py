#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""账号巡检异步编排：投递、执行记录查询与结果缓存读取。"""

from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from common.utils import get_logger

logger = get_logger(__name__)

SCAN_TASK_NAME = "identity.tasks.scan_account_risks_task"
RESULT_CACHE_PREFIX = "account_risk_scan_result_"
ERROR_CACHE_PREFIX = "account_risk_scan_error_"


def result_cache_key(task_id: Any) -> str:
    return f"{RESULT_CACHE_PREFIX}{task_id}"


def error_cache_key(task_id: Any) -> str:
    return f"{ERROR_CACHE_PREFIX}{task_id}"


def dispatch_account_risk_scan(operator: Any) -> Any:
    """创建执行记录并投递巡检任务（eager 模式同步执行，便于测试/E2E）。"""
    from identity.tasks import scan_account_risks_task  # noqa: F401 确保任务已注册
    from server.celery import app
    from task.models.task import TaskExecution

    execution = TaskExecution.objects.create(
        name=SCAN_TASK_NAME,
        kwargs={"operator_pk": getattr(operator, "pk", None)},
        creator=operator,
    )

    def _dispatch() -> None:
        app.send_task(
            SCAN_TASK_NAME,
            kwargs={"operator_pk": getattr(operator, "pk", None)},
            task_id=str(execution.pk),
        )

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        app.tasks[SCAN_TASK_NAME].apply(
            kwargs={"operator_pk": getattr(operator, "pk", None)},
            task_id=str(execution.pk),
        )
    else:
        transaction.on_commit(_dispatch)
    return execution


def scan_task_status(task_id: Any, operator: Any = None) -> dict[str, Any] | None:
    """按 task_id 取巡检进度；非本人且非超管一律当「不存在」处理（不泄露任务是否存在）。"""
    from task.models.task import TaskExecution

    execution = TaskExecution.objects.filter(pk=task_id, name=SCAN_TASK_NAME).first()
    if execution is None:
        return None
    if operator is not None and not getattr(operator, "is_superuser", False):
        owner_pk = execution.creator_id or (execution.kwargs or {}).get("operator_pk")
        if str(owner_pk) != str(getattr(operator, "pk", "")):
            return None
    return {
        "task_id": str(execution.pk),
        "status": execution.status,
        "progress": execution.progress,
        "stage": execution.stage,
        "result": cache.get(result_cache_key(execution.pk)),
        "error": cache.get(error_cache_key(execution.pk)) or "",
    }
