# -*- coding: utf-8 -*-
"""
异步记录（导出 / 导入 / 任务执行）的统计口径：近 N 天「总数 / 进行中 / 失败 / 最近一次」。

**为什么集中成纯函数**：三类记录的统计口径必须完全一致（否则聚合抽屉里「进行中」
一会算 REVOKED 一会不算），而各自 ViewSet 只负责「取哪个 queryset + 传什么差异项」。

- 取值域与列表同口径：超管看全量，其余按 `creator` 收口（与 `RecordOwnerFilter` 一致）；
- 状态集合可注入：导出/导入与任务执行的终态不同（任务执行多一个 REVOKED）；
- 时间窗可注入：单测可构造窗口边界，避免依赖 `freeze_time`。
"""

import datetime

from django.utils import timezone

__all__ = [
    "RECORD_STATS_CACHE_SECONDS",
    "RECORD_STATS_WINDOW_DAYS",
    "record_stats",
]

# 统计窗口（近 N 天）与短缓存时长：与审批 pending-count 的短缓存范式一致
RECORD_STATS_WINDOW_DAYS = 30
RECORD_STATS_CACHE_SECONDS = 10

# 默认状态集合：进行中 = 排队 + 运行中；失败 = 失败 + 撤销（撤销对任务执行是终态）
DEFAULT_IN_PROGRESS_STATUSES = ("PENDING", "RUNNING")
DEFAULT_FAILED_STATUSES = ("FAILURE", "REVOKED", "FAILED")


def record_stats(
    queryset,
    user,
    *,
    days: int = RECORD_STATS_WINDOW_DAYS,
    status_field: str = "status",
    creator_field: str = "creator",
    time_field: str = "created_time",
    name_field: str = "name",
    in_progress_statuses=DEFAULT_IN_PROGRESS_STATUSES,
    failed_statuses=DEFAULT_FAILED_STATUSES,
) -> dict:
    """统计某类异步记录在近 `days` 天内的执行情况。

    :param queryset: 该记录模型的全量 queryset（未做属主过滤）
    :param user: 当前用户（超管看全量，其余只看本人创建的）
    :return: ``{days, total, in_progress, failed, latest}``，`latest` 无记录时为 ``None``
    """
    since = timezone.now() - datetime.timedelta(days=days)
    scoped = queryset if getattr(user, "is_superuser", False) else queryset.filter(**{creator_field: user})
    window = scoped.filter(**{f"{time_field}__gte": since})

    latest = window.order_by(f"-{time_field}").values("pk", name_field, status_field, time_field).first()
    return {
        "days": days,
        "total": window.count(),
        "in_progress": window.filter(**{f"{status_field}__in": in_progress_statuses}).count(),
        "failed": window.filter(**{f"{status_field}__in": failed_statuses}).count(),
        "latest": _serialize_latest(latest, name_field, status_field, time_field),
    }


def _serialize_latest(latest, name_field, status_field, time_field):
    """最近一次记录归一化为固定键（各模型字段名不同，前端只认统一结构）。"""
    if not latest:
        return None
    created_time = latest.get(time_field)
    return {
        "pk": str(latest.get("pk") or ""),
        "name": latest.get(name_field) or "",
        "status": latest.get(status_field) or "",
        "created_time": created_time.isoformat() if created_time else None,
    }
