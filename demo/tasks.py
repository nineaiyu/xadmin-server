#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""demo 定时任务：演示「业务任务 + 任务管理页」的接入姿势。

二开抄这四条即可：

1. ``@shared_task`` 声明即注册（celery autodiscover 自动发现各 app 的 tasks.py），无需登记表；
2. 周期执行经 ``django_celery_beat.PeriodicTask`` 配置——``seed_demo_book`` 已灌入
   「示例-自动下架书籍」（默认停用），可在 系统 → 任务管理 页启停、改 cron、**立即运行**；
3. 幂等 + 返回可读统计：执行结果写入任务执行记录（TaskExecution），排障以记录为准；
4. 单一职责：任务只做状态流转；需要"人工确认"的场景走审批（见 views 的 412 演示），
   不要把审批判断塞进任务里。
"""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

# 默认阈值（天）：上架超过该天数仍未产生新动作的书籍视为滞销，自动下架回草稿
DEFAULT_OFF_SHELF_DAYS = 30


@shared_task
def auto_off_shelf_books(days: int = DEFAULT_OFF_SHELF_DAYS, dry_run: bool = False) -> str:
    """把上架超过 ``days`` 天的书籍自动下架（状态回草稿并停用）。

    - 任务管理页「立即运行」时按 PeriodicTask.kwargs 下发参数（默认 ``{"days": 30}``）；
    - ``dry_run=True`` 只统计不落库（演示"先看命中再决定"的安全阀）；
    - 返回可读统计字符串，写入任务执行记录。
    """
    from demo.models import Book

    cutoff = timezone.now() - timedelta(days=days)
    # 只处理「已上架且落过上架时间」的数据；软删数据被默认管理器排除
    queryset = Book.objects.filter(status=Book.Status.ON_SHELF, on_shelf_time__lt=cutoff)
    matched = queryset.count()
    if dry_run or not matched:
        detail = f"auto off shelf: matched={matched} updated=0 dry_run={dry_run}"
        logger.info(detail)
        return detail
    # QuerySet.update 不触发 auto_now：显式刷新 updated_time（也不动 modifier，保持"最后操作人"语义）
    updated = queryset.update(status=Book.Status.DRAFT, is_active=False, updated_time=timezone.now())
    detail = f"auto off shelf: matched={matched} updated={updated} (threshold={days}d)"
    logger.info(detail)
    return detail
