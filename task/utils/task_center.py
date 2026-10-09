#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务中心：统一任务视图 + 协作式取消 + 白名单重跑。

三类记录只读聚合（**不建新表**）：

- ``task``：``TaskExecution``（周期任务/异步任务执行历史）；
- ``export``：``ExportRecord``（导出 + 报表产物）；
- ``import``：``ImportRecord``。

取消口径（协作式）：

- 未开始（PENDING）→ 立即置 ``REVOKED`` 并 ``app.control.revoke``；
- 运行中（RUNNING）→ 下 ``revoke`` + 落取消标记，任务在安全点检查标记后自行收敛
  （导出/导入已在里程碑与逐行循环中接入 ``ensure_not_cancelled``）；
- 已终态 → 幂等返回，不改状态。

重跑口径（白名单，不开放任意重跑）：导出 / 导入 / 报表三类；新记录归属操作者，
重放原任务参数（视图路径由原记录的 ``path`` 反解），产物与审计与首次执行同链路。

执行历史（``TaskExecution``）已收敛到本页：任务日志页停用，其权限点迁到任务中心
菜单下；导出/导入记录投递时补建的同 pk 执行行由产物行承载，统一视图按 pk 排除
——同一件事只出现一行。顶栏任务中心抽屉保留为快捷入口。
"""

from typing import Any

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

TYPE_TASK = "task"
TYPE_EXPORT = "export"
TYPE_IMPORT = "import"
UNIFIED_TYPES = (TYPE_TASK, TYPE_EXPORT, TYPE_IMPORT)

CANCEL_FLAG_PREFIX = "task_cancel"
CANCEL_FLAG_TTL = 24 * 3600
#: 每类记录的候选窗口上限（跨类型合并排序的取数上界，防大表全扫）
MAX_UNIFIED_ROWS = 2000
#: 运行中状态集合
ACTIVE_STATUSES = ("PENDING", "RUNNING")
REPORT_MODULE = "Report"


class TaskCancelled(Exception):
    """协作式取消信号：任务在安全点发现取消标记后抛出，由任务收敛为 REVOKED。"""


# --------------------------------------------------------------- 取消标记


def _cancel_key(record_id: Any) -> str:
    return f"{CANCEL_FLAG_PREFIX}:{record_id}"


def request_cancel(record_id: Any) -> None:
    try:
        cache.set(_cancel_key(record_id), 1, CANCEL_FLAG_TTL)
    except Exception:  # noqa: BLE001 缓存不可用只影响协作式取消的即时性
        logger.warning("set task cancel flag failed: %s", record_id, exc_info=True)


def clear_cancel(record_id: Any) -> None:
    try:
        cache.delete(_cancel_key(record_id))
    except Exception:  # noqa: BLE001
        logger.debug("clear task cancel flag failed: %s", record_id, exc_info=True)


def is_cancel_requested(record_id: Any) -> bool:
    try:
        return bool(cache.get(_cancel_key(record_id)))
    except Exception:  # noqa: BLE001 缓存故障按未请求取消处理（fail-open，避免误杀任务）
        logger.warning("read task cancel flag failed: %s", record_id, exc_info=True)
        return False


def ensure_not_cancelled(record_id: Any) -> None:
    """任务安全点检查：命中取消标记即抛 ``TaskCancelled``。"""
    if is_cancel_requested(record_id):
        raise TaskCancelled(str(_("Task cancelled by user")))


def mark_execution_revoked(record_id: Any) -> None:
    """把同 pk 的执行历史行标记为 REVOKED 终态。

    先写 ``date_finished`` 即可让 ``task_postrun`` 信号（带 ``date_finished is null``
    守卫）不再把它覆盖成 SUCCESS——取消语义在统一视图里保持一致。
    """
    from task.models.task import TaskExecution

    try:
        TaskExecution.objects.filter(pk=record_id, date_finished__isnull=True).update(
            status="REVOKED", date_finished=timezone.now()
        )
    except Exception:  # noqa: BLE001 记账失败不影响取消本身
        logger.warning("mark task execution revoked failed: %s", record_id, exc_info=True)


# 实现拆至 task.utils.task_center_unified：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = (
    "_creator_name",
    "_dispatch",
    "_export_row",
    "_fetch",
    "_import_row",
    "_iso",
    "_owner_filter",
    "_rerun_export",
    "_rerun_import",
    "_rerun_report",
    "_stage_of",
    "_task_row",
    "_time_filters",
    "cancel_record",
    "progress_of",
    "rerun_record",
    "resolve_view_path",
    "unified_rows",
)


def __getattr__(name: Any) -> Any:
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("task.utils.task_center_unified"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
