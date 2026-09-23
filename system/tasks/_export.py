#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导出任务实现（显式请求上下文执行 export_data 并落产物）。

说明：任务函数本体保留在 ``system.tasks``（celery 任务名 = 函数 __module__，
必须保持 ``system.tasks.<name>`` 不变以便既有周期任务登记/日志/告警链路匹配），
本模块只承载实现体。请求经 ``build_task_request`` 显式构造（不再重放 WSGIRequest，
见 common/core/task_request.py 的契约清单）；导出为只读链路，
不写 creator/审计，因此不绑定 thread-local 请求。
"""

from django.conf import settings
from django.utils import translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from common.core.task_request import bind_view_task_context, build_task_request
from common.utils import get_logger
from common.utils.timezone import local_now_display
from system.utils.task_center import TaskCancelled, mark_execution_revoked

logger = get_logger(__name__)

# 导出产物 MIME：下载中心按记录后缀回写 Content-Type
EXPORT_MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


def build_export_request(record, query_params, user):
    """构造导出执行请求：显式 method/path/查询串 + 提交者身份直通。

    不在任务里重新签发 access token：任务排队可能超过 access token 寿命（默认 1h），
    重新签发的令牌会因过期导致认证失败。注入 user 后视图内的菜单/数据/字段三层权限与
    filterset 过滤仍按提交者身份生效。
    """
    return build_task_request(
        method="GET",
        path=record.path or "/",
        query_params=query_params or {},
        user=user,
    )


def _save_progress(record, percent, stage=""):
    """运行中进度落库（0-100）：P-2 统一助手（里程碑即协作式取消的安全点）。

    终态由任务结束分支覆盖（终态 100 不做取消检查，避免已完成的导出被翻成取消）。
    """
    from system.utils.task_progress import KIND_EXPORT, update_progress

    update_progress(KIND_EXPORT, record.pk, percent, stage=stage)
    # 同步调用方内存对象（后续分支可能基于 record 继续 save）
    record.progress = max(0, min(100, int(percent)))


def run_async_export(record_id, view_path, query_params, user_pk):
    """异步执行数据导出：重放 export_data 视图，产物落 UploadFile 供下载中心取用。

    记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
    TaskExecution 由 after_task_publish/prerun/postrun 信号自动记账，
    因此执行历史页与增量日志零成本复用。
    """
    from django.core.files.base import ContentFile

    from common.notifications import ExportDataMessage
    from system.models.export import ExportRecord
    from system.models.task import TaskExecution
    from system.models.upload import UploadFile
    from system.models.user import UserInfo

    record = ExportRecord.objects.filter(pk=record_id).first()
    if record is None:
        logger.warning("Export record not found: %s", record_id)
        return 0
    # 真实投递由 after_task_publish 自动记账；同步执行（apply/EAGER）不发该信号，此处补齐，
    # 保证执行历史页与增量日志在两种环境下都可用
    TaskExecution.objects.get_or_create(
        pk=record_id,
        defaults={"name": "system.tasks.async_export_data_task", "args": [record.name], "kwargs": query_params or {}},
    )
    user = UserInfo.objects.filter(pk=user_pk).first() if user_pk else None
    record.status = ExportRecord.Status.RUNNING
    # 导出为视图整体重放，无法逐行上报，仅里程碑粒度：RUNNING 10 → 计数完成 30 →
    # 内容渲染完成 80 → SUCCESS 100
    record.progress = 10
    record.save(update_fields=["status", "progress", "updated_time"])
    start_time, total, state = local_now_display(), 0, True
    try:
        request = build_export_request(record, query_params, user)
        translation.activate(translation.get_language_from_request(request))
        view_cls = import_string(view_path)

        # 行数走与导出同一套 filterset + 数据权限链，避免为计数做一次全量序列化
        probe = view_cls()
        bind_view_task_context(probe, request, action="export_data")
        total = probe.filter_queryset(probe.get_queryset()).count()
        logger.info("async export %s total rows: %s", view_path, total)
        _save_progress(record, 30, stage=_("Counting rows"))

        response = view_cls.as_view({"get": "export_data"})(request)
        response.render()
        if response.status_code != 200:
            raise ValueError(f"export view returned status {response.status_code}")
        content = response.content
        _save_progress(record, 80, stage=_("Rendering content"))

        filename = f"{record.name}.{record.file_format}"
        upload = UploadFile(
            filename=filename,
            filesize=len(content),
            mime_type=EXPORT_MIME_TYPES.get(record.file_format, "application/octet-stream"),
            is_tmp=True,
            is_upload=False,
            creator=user,
        )
        upload.filepath.save(filename, ContentFile(content), save=False)
        upload.save()
        record.file = upload
        record.rows = min(total, getattr(settings, "EXPORT_MAX_LIMIT", total))
        record.status = ExportRecord.Status.SUCCESS
        record.progress = 100
        record.error = None
        record.save(update_fields=["file", "rows", "status", "progress", "error", "updated_time"])
        logger.info("async export done: %s bytes, rows: %s", len(content), record.rows)
    except TaskCancelled as exc:
        # 协作式取消（P-2）：落 REVOKED 终态并同步执行历史行；不 re-raise（不是故障）
        state = False
        record.status = ExportRecord.Status.REVOKED
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        mark_execution_revoked(record.pk)
        logger.info("async export cancelled by user: %s", record_id)
    except Exception as exc:
        state = False
        record.status = ExportRecord.Status.FAILURE
        record.error = str(exc)[:2000]
        record.save(update_fields=["status", "error", "updated_time"])
        logger.exception("async export failed: %s", record_id)
        # 继续抛出，交给 celery 标记任务失败并触发 task_failure 告警
        raise
    finally:
        if user:
            try:
                ExportDataMessage(
                    user,
                    {
                        "task_name": record.name,
                        "state": state,
                        "status": _("Operation successful") if state else _("Operation failed"),
                        "tasks": [
                            {
                                "task_id": str(record.pk),
                                "start_time": start_time,
                                "end_time": local_now_display(),
                                "result": record.error or _("Exported {} rows").format(record.rows),
                            }
                        ],
                    },
                ).publish()
            except Exception:
                logger.warning("Send export data message failed", exc_info=True)
    return record.rows
