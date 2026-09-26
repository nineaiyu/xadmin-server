#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流域周期任务（自 system/tasks/__init__.py 随域迁出）。

任务名 = ``approval.tasks.<func>``：django_celery_beat 的历史条目由
register_as_period_task 的启动期对账自动清理重建（模块裁剪同机制），
无需数据迁移。任务模块经 approval/apps.py::ready() 显式 import 注册。
"""

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab="42 3 * * *", module="approval")
def auto_expire_approval_job():
    """敏感操作审批单超时（APPROVAL_PENDING_TIMEOUT，默认 3 天）置 EXPIRED。"""
    from approval.utils.approval import expire_pending_approvals

    count = expire_pending_approvals()
    if count:
        logger.info("Expire pending approvals: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="0 9 * * *", module="approval")
def auto_remind_approval_job():
    """待审批超时提醒（APPROVAL_REMIND_HOURS，默认 24h）：每日 09:00 对未处理且未提醒过的单补发一次。"""
    from approval.utils.approval import remind_pending_approvals

    count = remind_pending_approvals()
    if count:
        logger.info("Remind pending approvals: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="52 3 * * *", module="approval")
def auto_clean_approval_job():
    """清理超过保留期的审批单（APPROVAL_KEEP_DAYS，默认 180 天，分批删）。"""
    from approval.utils.approval import clean_expired_approvals

    removed = clean_expired_approvals()
    if removed:
        logger.info("Clean approval requests: %s rows", removed)
    return removed


@shared_task
@register_as_period_task(crontab="*/30 * * * *", module="approval_flow")
def auto_remind_approval_flow_job():
    """流程节点超时提醒：节点 timeout_hours 超时未处理，向指派人补发一次（每任务每日一次）。"""
    from approval.utils.approval_flow import remind_pending_tasks

    count = remind_pending_tasks()
    if count:
        logger.info("Remind pending approval flow tasks: %s rows", count)
    return count


@shared_task
@register_as_period_task(crontab="12 4 * * *", module="approval_flow")
def auto_clean_approval_flow_job():
    """清理超过保留期的流程实例（APPROVAL_FLOW_KEEP_DAYS，默认 365 天，分批删，级联任务）。"""
    from approval.utils.approval_flow import clean_finished_instances

    removed = clean_finished_instances()
    if removed:
        logger.info("Clean approval flow instances: %s rows", removed)
    return removed
