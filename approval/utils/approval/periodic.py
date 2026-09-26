#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：定时任务（超时过期 / 超时提醒 / 保留期清理）。"""

from common.utils import get_logger

from .approvers import get_approver_queryset, resolve_approvers
from .constants import APPROVAL_REMIND_CACHE_SECONDS
from .queries import invalidate_pending_count_cache

logger = get_logger(__name__)


def expire_pending_approvals(pending_days: int = None) -> int:
    """PENDING 超时置 EXPIRED（APPROVAL_PENDING_TIMEOUT，默认 3 天，清理任务调用）。"""
    import datetime

    from django.utils import timezone

    from approval.models import ApprovalRequest, ApprovalRequestStep
    from common.core.config import SysConfig

    if pending_days is None:
        pending_days = int(SysConfig.APPROVAL_PENDING_TIMEOUT)
    if not pending_days or pending_days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=pending_days)
    now = timezone.now()
    pks = list(
        ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING, created_time__lt=deadline).values_list(
            "pk", flat=True
        )
    )
    if not pks:
        return 0
    count = ApprovalRequest.objects.filter(pk__in=pks).update(
        status=ApprovalRequest.Status.EXPIRED, current_level=0, updated_time=now
    )
    # 多级链：在途级次统一作废（扁平单命中 0 行无副作用）+ 清当前级候选人投影
    ApprovalRequestStep.objects.filter(request_id__in=pks, status=ApprovalRequestStep.Status.PENDING).update(
        status=ApprovalRequestStep.Status.CANCELLED, updated_time=now
    )
    ApprovalRequest.current_assignees.through.objects.filter(approvalrequest_id__in=pks).delete()
    invalidate_pending_count_cache()
    return count


def remind_pending_approvals(remind_hours: int = None) -> int:
    """超时未处理的 PENDING 单向审批人补发一次提醒，返回提醒过的单数。

    - 阈值 = APPROVAL_REMIND_HOURS（默认 24h，0 = 不提醒）；
    - 同一单只提醒一次（缓存占位；仅在至少成功推送给一个审批人后占位，
      通知链路瞬时故障不会让该单永久失去提醒）；
    - 单条推送失败只记日志，不阻断其余单。
    """
    import datetime

    from django.core.cache import cache
    from django.utils import timezone

    from approval.models.approval import ApprovalRequest
    from common.core.config import SysConfig
    from system.notifications import ApprovalRequestMessage

    hours = int(SysConfig.APPROVAL_REMIND_HOURS) if remind_hours is None else int(remind_hours)
    if not hours or hours <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(hours=hours)
    reminded = 0
    queryset = (
        ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING, created_time__lt=deadline)
        .select_related("creator")
        .order_by("created_time")
    )
    for approval in queryset.iterator():
        cache_key = f"approval_remind_{approval.pk}"
        if cache.get(cache_key):
            continue
        # 多级链只提醒当前级候选人；扁平单保持全局审批人口径
        if (approval.current_level or 0) > 0:
            users = list(approval.current_assignees.all())
        else:
            users = list(resolve_approvers(approval.creator) if approval.creator else get_approver_queryset())
        delivered = False
        for user in users:
            try:
                ApprovalRequestMessage(user, "remind", approval).publish(is_async=True)
                delivered = True
            except Exception:  # noqa: BLE001 单条推送失败不阻断其余单
                logger.warning("send approval remind failed. approval:%s user:%s", approval.pk, user.pk, exc_info=True)
        if delivered:
            cache.set(cache_key, 1, APPROVAL_REMIND_CACHE_SECONDS)
            reminded += 1
    return reminded


def clean_expired_approvals(keep_days: int = None, batch_size: int = 2000) -> int:
    """清理超过保留期的审批单（APPROVAL_KEEP_DAYS，默认 180 天，分批删）。"""
    import datetime

    from django.db import transaction
    from django.utils import timezone

    from approval.models.approval import ApprovalRequest
    from common.core.config import SysConfig

    if keep_days is None:
        keep_days = int(SysConfig.APPROVAL_KEEP_DAYS)
    if not keep_days or keep_days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    total = 0
    while True:
        pks = list(ApprovalRequest.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        with transaction.atomic():
            deleted, _rows = ApprovalRequest.objects.filter(pk__in=pks).delete()
        total += deleted
    return total
