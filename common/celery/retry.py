# -*- coding: utf-8 -*-
"""Celery 任务重试统一范式（选型与约束见 docs/框架开发遵循准则.md §1.8）。

两种范式，禁止混用（双轨重试会放大投递次数）：

1. **自动退避重试**（外部 IO、无状态、重试安全）：`retry_or_log` ——
   指数退避 `min(base × 2^retries, max_seconds)`；同步直接调用路径
   （``called_directly``）不重试，保持「后台失败不打断业务」语义。
2. **应用层状态机重试**（有投递状态/审计诉求，如 ``system/webhook_tasks.deliver_webhook``）：
   celery 层 ``max_retries=0``，由业务表（attempt / next_retry_at / exhausted）跟踪
   重试与耗尽告警，状态可查询、可人工重放。
"""

from common.utils import get_logger

logger = get_logger(__name__)


def retry_or_log(task_self, exc, *, what: str, base_seconds: int = 60, max_seconds: int = 600, max_retries: int = 3):
    """外部 IO 任务的统一退避重试；耗尽或同步调用时记录错误并返回 False（不外抛）。

    返回 True 前必然以 ``task.retry`` 抛出（任务以 RETRY 状态结束，由 celery 排期）；
    返回 False 表示本次执行终止：调用方按后台任务语义收尾（如 ``return None``）。
    """
    direct = task_self is None or getattr(task_self.request, "called_directly", False)
    if not direct and task_self.request.retries < max_retries:
        countdown = min(base_seconds * (2**task_self.request.retries), max_seconds)
        logger.warning(f"{what} failed, retry in {countdown}s: {exc}")
        raise task_self.retry(exc=exc, countdown=countdown) from exc
    logger.error(f"{what} error: {exc}")
    return False
