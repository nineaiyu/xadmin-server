#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""账号安全巡检异步任务（长耗时扫描从请求线程移出，进度写入 TaskExecution）。"""

from typing import Any

from celery import shared_task

from common.utils import get_logger

logger = get_logger(__name__)

SCAN_TASK_NAME = "identity.tasks.scan_account_risks_task"


@shared_task(bind=True, name=SCAN_TASK_NAME)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery）无类型存根：函数自身标注完整
def scan_account_risks_task(self: Any, operator_pk: Any = None) -> dict[str, Any]:
    from django.core.cache import cache
    from django.utils.translation import gettext as _

    from identity.models import UserInfo
    from identity.utils.account_risk import scan_account_risks
    from identity.utils.account_risk_scan import error_cache_key, result_cache_key
    from task.utils.task_progress import KIND_TASK, update_progress

    task_id = str(getattr(self.request, "id", "") or "")
    operator = UserInfo.objects.filter(pk=operator_pk).first() if operator_pk else None
    try:
        result = scan_account_risks(
            operator=operator,
            progress=lambda percent, stage: update_progress(KIND_TASK, task_id, percent, stage=stage),
        )
        cache.set(result_cache_key(task_id), result, 3600)
        update_progress(KIND_TASK, task_id, 100, stage=str(_("Completed")))
        return result
    except Exception as exc:  # noqa: BLE001 任务失败需把可读原因留给轮询端
        cache.set(error_cache_key(task_id), str(exc), 3600)
        logger.exception("account risk scan task failed: %s", task_id)
        raise
