#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导出任务实现（重放 export_data 视图并落产物）。

说明：任务函数本体保留在 ``system.tasks``（celery 任务名 = 函数 __module__，
必须保持 ``system.tasks.<name>`` 不变以便既有周期任务登记/日志/告警链路匹配），
本模块只承载实现体。
"""

from io import BytesIO
from urllib.parse import urlencode

from django.conf import settings
from django.core.handlers.wsgi import WSGIRequest
from django.utils import translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from common.utils.timezone import local_now_display

logger = get_logger(__name__)

# 导出产物 MIME：下载中心按记录后缀回写 Content-Type
EXPORT_MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


def build_export_request(record, query_params, user):
    """构造用于重放 export_data 的原始请求。

    直接注入提交者身份（DRF ForcedAuthentication），不在任务里重新签发 access
    token：任务排队可能超过 access token 寿命（默认 1h），重新签发的令牌会因
    过期导致重放认证失败。注入 user 后视图内的菜单/数据/字段三层权限与
    filterset 过滤仍按提交者身份生效。
    """
    environ = {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": record.path or "/",
        "QUERY_STRING": urlencode(query_params or {}, doseq=True),
        "SERVER_NAME": "xadmin",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "xadmin",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": BytesIO(),
        "wsgi.url_scheme": "http",
    }
    request = WSGIRequest(environ)
    if user:
        # DRF 初始化 Request 时检测到 _force_auth_user，改用 ForcedAuthentication，
        # 跳过 JWT 解析直接以该用户身份执行后续权限链
        request._force_auth_user = user
    return request


def _save_progress(record, percent):
    """运行中进度落库（0-100）：进度条数据源，终态由任务结束分支覆盖。"""
    percent = max(0, min(100, int(percent)))
    if record.progress != percent:
        record.progress = percent
        record.save(update_fields=["progress", "updated_time"])


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
        from rest_framework.request import Request

        probe = view_cls()
        probe.action = "export_data"
        probe.kwargs = {}
        probe.format_kwarg = None
        probe.request = Request(request, parsers=[])
        if user:
            probe.request.user = user
        total = probe.filter_queryset(probe.get_queryset()).count()
        logger.info("async export %s total rows: %s", view_path, total)
        _save_progress(record, 30)

        response = view_cls.as_view({"get": "export_data"})(request)
        response.render()
        if response.status_code != 200:
            raise ValueError(f"export view returned status {response.status_code}")
        content = response.content
        _save_progress(record, 80)

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
