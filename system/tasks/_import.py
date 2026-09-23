#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导入任务实现（逐行 savepoint 导入 + 失败行报告）。

说明：任务函数本体保留在 ``system.tasks``（celery 任务名 = 函数 __module__，
必须保持 ``system.tasks.<name>`` 不变以便既有周期任务登记/日志/告警链路匹配），
本模块只承载实现体。
"""

from django.db import transaction
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from common.core.task_request import bind_view_task_context, build_task_request
from common.utils import get_logger
from common.utils.timezone import local_now_display
from server.utils import set_current_request
from system.utils.task_center import TaskCancelled, ensure_not_cancelled, mark_execution_revoked

from ._export import EXPORT_MIME_TYPES

logger = get_logger(__name__)


class _ImportAborted(Exception):
    """失败率超限中止：触发外层事务回滚（成功行一并撤销）。"""


def _upload_import_error_report(record, user, column_titles, errors):
    """失败行错误报告落 UploadFile(is_tmp=True)，返回实例。"""
    import os
    import tempfile

    from django.core.files.base import ContentFile

    from system.models.upload import UploadFile
    from system.utils.import_report import build_error_report

    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        build_error_report(tmp_path, column_titles, errors)
        with open(tmp_path, "rb") as fp:
            content = fp.read()
    finally:
        os.remove(tmp_path)
    filename = f"{record.name}_errors.xlsx"
    upload = UploadFile(
        filename=filename,
        filesize=len(content),
        mime_type=EXPORT_MIME_TYPES["xlsx"],
        is_tmp=True,
        is_upload=False,
        creator=user,
    )
    upload.filepath.save(filename, ContentFile(content), save=False)
    upload.save()
    return upload


def _import_row(view, action_type, row):
    """单行导入：校验 + 写入，失败抛异常由调用方 savepoint 回滚。"""
    from rest_framework.exceptions import ValidationError

    if action_type == "update":
        instance = view.filter_queryset(view.get_queryset()).filter(pk=row.get("pk")).first()
        if instance is None:
            raise ValidationError(_("Object not found: {}").format(row.get("pk")))
        serializer = view.get_serializer(instance, data=row, partial=True)
    else:
        serializer = view.get_serializer(data=row)
    serializer.is_valid(raise_exception=True)
    if action_type == "update":
        view.perform_update(serializer)
    else:
        view.perform_create(serializer)


def run_async_import(record_id, view_path, user_pk):
    """异步执行数据导入：任务内解析源文件，逐行 savepoint 导入并生成失败行报告。

    - 记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
      TaskExecution 在同步执行（apply/EAGER）时补建，执行历史/增量日志双环境可用；
    - 逐行独立 savepoint：单行失败回滚该行继续下一行；失败率超
      IMPORT_FAIL_RATE_LIMIT（默认 0.5，0=不限制）时中止并回滚全部成功行；
    - 校验/写入复用目标视图的 serializer（字段权限/联动校验同源），
      threadlocal 请求注入保证 creator 信号正常赋值。
    """
    from common.core.config import SysConfig
    from common.notifications import ImportDataMessage
    from system.models.import_ import ImportRecord
    from system.models.task import TaskExecution
    from system.models.user import UserInfo
    from system.utils.import_progress import clear_import_progress
    from system.utils.task_progress import KIND_IMPORT, update_progress

    record = ImportRecord.objects.filter(pk=record_id).first()
    if record is None:
        logger.warning("Import record not found: %s", record_id)
        return 0
    TaskExecution.objects.get_or_create(
        pk=record_id,
        defaults={"name": "system.tasks.async_import_data_task", "args": [record.name], "kwargs": record.params or {}},
    )
    user = UserInfo.objects.filter(pk=user_pk).first() if user_pk else None
    record.status = ImportRecord.Status.RUNNING
    record.save(update_fields=["status", "updated_time"])
    start_time, state = local_now_display(), True
    total = success_rows = 0
    errors, column_titles = [], []
    aborted, abort_reason = False, None
    try:
        if not record.source_file or not record.source_file.filepath:
            raise ValueError(_("Import source file not found"))
        source_path = record.source_file.filepath.path

        view_cls = import_string(view_path)
        view = view_cls()
        # 显式请求上下文（不再重放 WSGIRequest）：五个契约集中在 task_request 装配点，
        # 契约清单与守护测试见 common/core/task_request.py
        request = build_task_request(method="POST", path=record.path or "/", user=user)
        drf_request = bind_view_task_context(view, request, action="import_data")
        # 契约 5：thread-local 请求（creator 信号赋值 + 操作审计 request_uuid），出口处清理
        set_current_request(drf_request)

        # 行数据在 action 内已由文件解析器解析并序列化为 JSON（与同步导入同一条解析链）
        import json

        with open(source_path, encoding="utf-8") as fp:
            rows = json.load(fp)
        column_titles = (record.params or {}).get("column_titles") or []
        total = len(rows)
        if rows:
            # 自关联依赖拓扑排序（与同步导入 import_data 同口径，父行先建）
            from common.core.utils import has_self_fields, topological_sort

            self_field = has_self_fields(view.get_queryset().model, rows[0].keys())
            if self_field:
                rows = topological_sort(rows, parent=self_field)

        fail_rate_limit = SysConfig.IMPORT_FAIL_RATE_LIMIT
        # 运行期进度走缓存通道：本循环包在外层事务里，事务提交前其他连接读不到
        # 库内进度（见 system/utils/import_progress 模块说明），因此不写库、只写缓存
        last_percent = -1
        try:
            with transaction.atomic():
                for idx, row in enumerate(rows, start=1):
                    # 协作式取消（P-2）：逐行循环即安全点，取消触发外层事务回滚
                    ensure_not_cancelled(record.pk)
                    try:
                        with transaction.atomic():
                            _import_row(view, record.action, row)
                        success_rows += 1
                    except Exception as exc:
                        errors.append(
                            {
                                "row": idx,
                                "values": {str(k): row.get(k) for k in row},
                                "error": str(exc)[:500],
                            }
                        )
                        if fail_rate_limit and fail_rate_limit > 0 and failed_rate(errors, total) > fail_rate_limit:
                            aborted = True
                            abort_reason = _("Aborted: failure rate exceeds limit ({}/{} rows failed)").format(
                                len(errors), total
                            )
                            break
                    # 分批上报进度（1% 粒度，P-2 统一助手：导入运行期走缓存通道），供下载中心进度条展示
                    percent = int(idx / max(total, 1) * 100)
                    if percent != last_percent:
                        last_percent = percent
                        update_progress(KIND_IMPORT, record.pk, percent)
                if aborted:
                    # 外层事务回滚：已写入的成功行一并撤销
                    raise _ImportAborted(abort_reason)
        except _ImportAborted:
            pass
    except TaskCancelled as exc:
        # 协作式取消（P-2）：外层事务已回滚，落 REVOKED 终态（不是故障，不 re-raise）
        state = False
        record.status = ImportRecord.Status.REVOKED
        record.error = str(exc)[:2000]
        record.total = record.total or total
        record.save(update_fields=["status", "error", "total", "updated_time"])
        clear_import_progress(record.pk)
        mark_execution_revoked(record.pk)
        logger.info("async import cancelled by user: %s", record_id)
    except Exception as exc:
        state = False
        record.status = ImportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.total = record.total or total
        record.save(update_fields=["status", "error", "total", "updated_time"])
        clear_import_progress(record.pk)
        logger.exception("async import failed: %s", record_id)
        raise
    finally:
        set_current_request(None)

    # 走到这里：解析成功（含失败率中止回滚场景），推进终态与报告
    try:
        record.total = total
        record.success_rows = 0 if aborted else success_rows
        record.failed_rows = len(errors)
        if aborted:
            record.status = ImportRecord.Status.FAILURE
            record.error = abort_reason
        else:
            record.status = ImportRecord.Status.SUCCESS
            record.progress = 100
            record.error = None
        if errors and column_titles:
            record.error_report = _upload_import_error_report(record, user, column_titles, errors)
        record.save(
            update_fields=[
                "total",
                "success_rows",
                "failed_rows",
                "status",
                "progress",
                "error",
                "error_report",
                "updated_time",
            ]
        )
        logger.info(
            "async import done: total %s, success %s, failed %s, aborted %s",
            total,
            record.success_rows,
            record.failed_rows,
            aborted,
        )
    except Exception:
        logger.exception("async import finalize failed: %s", record_id)
        raise
    finally:
        # 终态已落库，清掉运行期缓存进度（序列化器 RUNNING 时才读缓存）
        clear_import_progress(record.pk)
        if user:
            try:
                ImportDataMessage(
                    user,
                    {
                        "task_name": record.name,
                        "view_doc": record.module or record.name,
                        "state": state,
                        "status": _("Operation successful") if state else _("Operation failed"),
                        "tasks": [
                            {
                                "task_id": str(record.pk),
                                "start_time": start_time,
                                "end_time": local_now_display(),
                                "result": record.error
                                or _("Imported {} rows, {} failed").format(record.success_rows, record.failed_rows),
                            }
                        ],
                    },
                ).publish()
            except Exception:
                logger.warning("Send import data message failed", exc_info=True)
    return record.success_rows


def failed_rate(errors, total):
    """当前失败率（total 防零）。"""
    return len(errors) / max(total, 1)
