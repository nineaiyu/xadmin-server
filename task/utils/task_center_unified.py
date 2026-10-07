#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务中心统一聚合视图（任务/导出/导入三源合并，自 task_center 拆分，行为不变）。"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from task.utils.task_center import (
    ACTIVE_STATUSES,
    MAX_UNIFIED_ROWS,
    REPORT_MODULE,
    TYPE_EXPORT,
    TYPE_IMPORT,
    TYPE_TASK,
    UNIFIED_TYPES,
    request_cancel,
)

logger = get_logger(__name__)


# --------------------------------------------------------------- 统一视图


def _owner_filter(queryset, user):
    """数据域与下载中心一致：超管全量，其余只看自己创建的记录。"""
    if getattr(user, "is_superuser", False):
        return queryset
    return queryset.filter(creator=user)


def _time_filters(queryset, start, end):
    if start:
        queryset = queryset.filter(created_time__gte=start)
    if end:
        queryset = queryset.filter(created_time__lte=end)
    return queryset


def progress_of(record, kind: str):
    """记录进度（0-100；任务执行无进度语义返回 None）。"""
    if kind == TYPE_TASK:
        return None
    if kind == TYPE_IMPORT and record.status == "RUNNING":
        from task.utils.import_progress import get_import_progress

        cached = get_import_progress(record.pk)
        if cached is not None:
            return int(cached)
    return int(getattr(record, "progress", 0) or 0)


def _creator_name(record) -> str:
    creator = getattr(record, "creator", None)
    return str(getattr(creator, "username", "") or "")


def _iso(value):
    return value.isoformat() if value else None


def _stage_of(record) -> str:
    """阶段描述（统一进度助手写入；任务执行无阶段语义返回空串）。"""
    return str(getattr(record, "stage", "") or "")


def _task_row(record) -> dict:
    time_cost = record.time_cost
    return {
        "type": TYPE_TASK,
        "pk": str(record.pk),
        "name": str(record.name or ""),
        "module": "",
        # 执行历史无「来源模块」，所属定时任务单独成列（对手动执行留空）
        "periodic_task": str(getattr(record.periodic_task, "name", "") or ""),
        "status": str(record.status),
        "progress": progress_of(record, TYPE_TASK),
        "stage": _stage_of(record),
        "time_cost": round(time_cost, 3) if time_cost is not None else None,
        "creator": _creator_name(record),
        "created_time": _iso(record.created_time),
        "finished_time": _iso(record.date_finished),
        "error": "",
        "has_file": False,
        "can_cancel": str(record.status) in ACTIVE_STATUSES,
        "can_rerun": False,
        # 执行历史可在本页清理；产物记录（导出/导入）的删除仍在下载中心
        "can_delete": True,
    }


def _export_row(record) -> dict:
    return {
        "type": TYPE_EXPORT,
        "pk": str(record.pk),
        "name": str(record.name or ""),
        "module": str(record.module or ""),
        "status": str(record.status),
        "progress": progress_of(record, TYPE_EXPORT),
        "stage": _stage_of(record),
        "creator": _creator_name(record),
        "created_time": _iso(record.created_time),
        "finished_time": _iso(record.updated_time) if str(record.status) not in ACTIVE_STATUSES else None,
        "error": str(record.error or "")[:500],
        "has_file": bool(record.file_id),
        "periodic_task": "",
        "time_cost": None,
        "can_cancel": str(record.status) in ACTIVE_STATUSES,
        "can_rerun": str(record.status) not in ACTIVE_STATUSES,
        "can_delete": False,
    }


def _import_row(record) -> dict:
    return {
        "type": TYPE_IMPORT,
        "pk": str(record.pk),
        "name": str(record.name or ""),
        "module": str(record.module or ""),
        "status": str(record.status),
        "progress": progress_of(record, TYPE_IMPORT),
        "stage": _stage_of(record),
        "creator": _creator_name(record),
        "created_time": _iso(record.created_time),
        "finished_time": _iso(record.updated_time) if str(record.status) not in ACTIVE_STATUSES else None,
        "error": str(record.error or "")[:500],
        "has_file": bool(record.error_report_id),
        "total": record.total,
        "success_rows": record.success_rows,
        "failed_rows": record.failed_rows,
        "periodic_task": "",
        "time_cost": None,
        "can_cancel": str(record.status) in ACTIVE_STATUSES,
        "can_rerun": bool(record.source_file_id) and str(record.status) not in ACTIVE_STATUSES,
        "can_delete": False,
    }


def unified_rows(user, *, types=None, status="", keyword="", creator="", start=None, end=None, page=1, size=15):
    """跨类型合并的任务行（按创建时间倒序 + 分页）。

    每类型先按各类型过滤条件取候选窗口（上限 ``MAX_UNIFIED_ROWS``）再合并排序，
    避免为统一视图引入第四张表或跨库 JOIN。
    """
    from django.db.models import Exists, OuterRef, Q

    from task.models.export import ExportRecord
    from task.models.import_ import ImportRecord
    from task.models.task import TaskExecution

    kinds = [kind for kind in (types or UNIFIED_TYPES) if kind in UNIFIED_TYPES]
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 15), 100))
    # 每类型取数窗口 = 已翻过的页数 × 页大小：合并后第 N 页的行必然落在各自类型
    # 前 N×size 行内（各类型自身按创建时间倒序），无需全表扫描即可保证分页正确性
    window = min(MAX_UNIFIED_ROWS, page * size)

    def _filtered(queryset, keyword_q):
        queryset = _time_filters(_owner_filter(queryset, user), start, end)
        if status:
            queryset = queryset.filter(status=status)
        if keyword:
            queryset = queryset.filter(keyword_q)
        if creator:
            queryset = queryset.filter(creator__username__icontains=creator)
        return queryset.order_by("-created_time")

    rows = []
    total = 0
    if TYPE_TASK in kinds:
        # 同一主键契约：导出/导入记录投递时会自动补建同 pk 的 TaskExecution
        # （pk = celery task_id），这部分执行历史已由产物行承载 —— 统一视图按 pk
        # 排除，否则同一个任务会显示两行（「任务」+「导出/导入」）。
        product_exists = Exists(ExportRecord.objects.filter(pk=OuterRef("pk"))) | Exists(
            ImportRecord.objects.filter(pk=OuterRef("pk"))
        )
        queryset = _filtered(
            TaskExecution.objects.annotate(_has_product=product_exists).filter(_has_product=False),
            Q(name__icontains=keyword) | Q(periodic_task__name__icontains=keyword),
        )
        total += queryset.count()
        rows += [_task_row(row) for row in queryset.select_related("periodic_task", "creator")[:window]]
    if TYPE_EXPORT in kinds:
        queryset = _filtered(
            ExportRecord.objects.all(),
            Q(name__icontains=keyword) | Q(module__icontains=keyword),
        )
        total += queryset.count()
        rows += [_export_row(row) for row in queryset.select_related("creator", "file")[:window]]
    if TYPE_IMPORT in kinds:
        queryset = _filtered(
            ImportRecord.objects.all(),
            Q(name__icontains=keyword) | Q(module__icontains=keyword),
        )
        total += queryset.count()
        rows += [_import_row(row) for row in queryset.select_related("creator", "source_file")[:window]]
    rows.sort(key=lambda row: row["created_time"] or timezone.now(), reverse=True)
    offset = (page - 1) * size
    return rows[offset : offset + size], total


# --------------------------------------------------------------- 取消


def _fetch(user, kind: str, pk: str):
    from task.models.export import ExportRecord
    from task.models.import_ import ImportRecord
    from task.models.task import TaskExecution

    model = {TYPE_TASK: TaskExecution, TYPE_EXPORT: ExportRecord, TYPE_IMPORT: ImportRecord}.get(kind)
    if model is None:
        return None
    try:
        return _owner_filter(model.objects.all(), user).filter(pk=pk).first()
    except (ValueError, TypeError, DjangoValidationError):
        # 主键形态非法（如 UUID 表收到非 UUID 串）：按「不可达」处理，不抛 500
        return None


def cancel_record(user, kind: str, pk: str) -> dict:
    """取消记录（幂等）：返回 ``{ok, detail}``。"""
    from server.celery import app

    record = _fetch(user, kind, pk)
    if record is None:
        return {"ok": False, "detail": str(_("The task does not exist or you do not have permission to access it"))}
    status = str(getattr(record, "status", ""))
    if status not in ACTIVE_STATUSES:
        return {"ok": True, "detail": str(_("The task is already finished"))}
    request_cancel(record.pk)
    try:
        app.control.revoke(str(record.pk), terminate=False)
    except Exception:  # noqa: BLE001 broker 不可达只影响协作式取消的即时性
        logger.warning("revoke celery task failed: %s", record.pk, exc_info=True)
    if status == "PENDING":
        # 尚未被 worker 取走：直接落终态（无需等协作点）
        record.status = "REVOKED"
        update_fields = ["status", "updated_time"]
        if hasattr(record, "date_finished"):
            record.date_finished = timezone.now()
            update_fields.append("date_finished")
        if hasattr(record, "progress"):
            update_fields.append("progress")
        record.save(update_fields=update_fields)
        return {"ok": True, "detail": str(_("Task cancelled"))}
    return {"ok": True, "detail": str(_("Stop requested; it takes effect at the next safe point"))}


# --------------------------------------------------------------- 重跑


def resolve_view_path(url_path: str) -> str:
    """记录里的请求路径 → 视图类点分路径（重跑复用同一视图重放链路）。"""
    from django.urls import Resolver404, resolve

    path = str(url_path or "").split("?", 1)[0]
    if not path:
        return ""
    try:
        match = resolve(path)
    except Resolver404:
        return ""
    view_cls = getattr(getattr(match, "func", None), "cls", None)
    if view_cls is None:
        return ""
    return f"{view_cls.__module__}.{view_cls.__name__}"


def _dispatch(task, args=None, kwargs=None, task_id=None):
    """派发任务：EAGER（测试/E2E）走同步 apply，否则 apply_async。"""
    from django.conf import settings
    from django.db import transaction

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return task.apply(args=args or [], kwargs=kwargs or {}, task_id=task_id)
    transaction.on_commit(lambda: task.apply_async(args=args or [], kwargs=kwargs or {}, task_id=task_id))
    return None


def rerun_record(user, kind: str, pk: str) -> dict:
    """重跑记录（导出 / 导入 / 报表）：新记录 + 同链路重放（以操作者身份执行）。"""
    record = _fetch(user, kind, pk)
    if record is None:
        return {"ok": False, "detail": str(_("The task does not exist or you do not have permission to access it"))}
    if str(getattr(record, "status", "")) in ACTIVE_STATUSES:
        return {"ok": False, "detail": str(_("The task is still running; stop it before rerunning"))}
    try:
        if kind == TYPE_TASK:
            return {"ok": False, "detail": str(_("Rerun is not supported for this task type"))}
        if kind == TYPE_IMPORT:
            return _rerun_import(record, user)
        if str(record.module or "") == REPORT_MODULE:
            return _rerun_report(record)
        return _rerun_export(record, user)
    except Exception as exc:  # noqa: BLE001 重跑失败归一为可读文案
        logger.warning("rerun task failed. kind:%s pk:%s", kind, pk, exc_info=True)
        return {"ok": False, "detail": str(exc)}


def _rerun_export(record, user) -> dict:
    from task.models.export import ExportRecord

    view_path = resolve_view_path(record.path)
    if not view_path or not record.params:
        return {"ok": False, "detail": str(_("The original export request cannot be replayed"))}
    clone = ExportRecord.objects.create(
        name=f"{record.name}-rerun",
        module=record.module,
        path=record.path,
        file_format=record.file_format,
        params=record.params,
        status=ExportRecord.Status.PENDING,
    )
    task = import_string("system.tasks.async_export_data_task")
    _dispatch(task, args=[str(clone.pk), view_path, record.params, str(getattr(user, "pk", "") or "")])
    return {"ok": True, "detail": str(_("Rerun submitted")), "data": {"record_id": str(clone.pk)}}


def _rerun_import(record, user) -> dict:
    from task.models.import_ import ImportRecord

    view_path = resolve_view_path(record.path)
    if not view_path or not record.source_file_id:
        return {"ok": False, "detail": str(_("The original import request cannot be replayed"))}
    clone = ImportRecord.objects.create(
        name=f"{record.name}-rerun",
        module=record.module,
        path=record.path,
        action=record.action,
        params=record.params,
        source_file=record.source_file,
        status=ImportRecord.Status.PENDING,
    )
    task = import_string("system.tasks.async_import_data_task")
    _dispatch(task, args=[str(clone.pk), view_path, str(getattr(user, "pk", "") or "")])
    return {"ok": True, "detail": str(_("Rerun submitted")), "data": {"record_id": str(clone.pk)}}


def _rerun_report(record) -> dict:
    from dataset.analysis_tasks import _precreate_record, run_scheduled_report
    from dataset.models.dataset import Report

    report_id = str((record.params or {}).get("report_id") or "")
    report = Report.objects.filter(pk=report_id).first() if report_id else None
    if report is None:
        return {"ok": False, "detail": str(_("The original report no longer exists"))}
    new_record = _precreate_record(report)
    # 重跑与「run 动作」同为手动触发：不推进调度簿记 last_run_at（bookkeep_schedule=False），
    # last_status 照常写——否则重跑恰好落在到期点与派发扫描之间会吞掉当期投递
    _dispatch(
        run_scheduled_report, kwargs={"report_id": str(report.pk), "bookkeep_schedule": False}, task_id=new_record
    )
    return {"ok": True, "detail": str(_("Rerun submitted")), "data": {"record_id": str(new_record)}}
