#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""统一导出服务：MIME 注册表 + 产物落库 + 进度/取消协议 + 异步导出编排。

全站导出/下载中心链路的单一出口，两条消费链各留薄适配：

- **视图重放链**（本模块 ``run_async_export``）：视图动作（export-async）落
  ExportRecord 后派发任务，任务经 ``build_task_request`` 显式构造请求（不再重放
  WSGIRequest，见 packages/xadmin-common/common/core/task_request.py 的契约清单）重放 export_data 视图；
  导出为只读链路，不写 creator/审计，因此不绑定 thread-local 请求。任务函数本体
  保留在 ``system.tasks``（celery 任务名 = 函数 __module__，必须保持
  ``system.tasks.<name>`` 不变以便既有周期任务登记/日志/告警链路匹配）；
- **定时报表链**（dataset 域）：渲染器在 dataset（报表工作簿与视图重放产物
  形态不同），产物落库 / 进度 / 取消协议复用本模块（``persist_export_artifact`` /
  ``update_progress`` / ``TaskCancelled`` 收敛）。

产物统一落 UploadFile(is_tmp=True)（复用临时文件定时清理），MIME 一律经
``EXPORT_MIME_TYPES`` 取值——下载中心按记录后缀回写 Content-Type 的唯一来源。
"""

from typing import Any

from django.conf import settings
from django.utils import translation
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from common.core.task_request import bind_view_task_context, build_task_request
from common.utils import get_logger
from common.utils.timezone import local_now_display
from task.utils.task_center import TaskCancelled, mark_execution_revoked

logger = get_logger(__name__)

# 导出产物 MIME 注册表（全站单源）：下载中心按记录后缀回写 Content-Type
EXPORT_MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}


def mime_type_for(file_format: Any) -> str:
    """按导出格式取产物 MIME（未登记格式回落通用二进制流）。"""
    return EXPORT_MIME_TYPES.get(str(file_format or "").lower(), "application/octet-stream")


def persist_export_artifact(record: Any, filename: str, content: Any, user: Any = None) -> Any:
    """导出产物落 UploadFile(is_tmp=True) 并挂接到下载中心记录。

    产物形态有两种：同步渲染留下的 ``bytes``，与流式导出产生的**文件对象**
    （``export_data`` 现在是 FileResponse，流式内容落地后以文件句柄传入）。
    两种情况都走同一套落盘与 mime 回写，调用方不必关心形态。
    """
    import os

    from django.core.files.base import ContentFile
    from django.core.files.base import File as DjangoFile

    from file.services import UploadFile

    if isinstance(content, (bytes, bytearray)):
        filesize = len(content)
        payload: Any = ContentFile(bytes(content))
    else:
        # 文件对象：尺寸按内容长度计（不依赖 .name，匿名临时文件没有可用路径）
        content.seek(0, os.SEEK_END)
        filesize = content.tell()
        content.seek(0)
        payload = DjangoFile(content, name=filename)

    upload = UploadFile(
        filename=filename,
        filesize=filesize,
        mime_type=mime_type_for(record.file_format),
        is_tmp=True,
        is_upload=False,
        creator=user,
    )
    upload.filepath.save(filename, payload, save=False)
    upload.save()
    record.file = upload
    return upload


def _drain_streaming_response(response: Any) -> Any:
    """把流式导出的分块内容落到匿名临时文件（``TemporaryFile``：无目录项、自动回收）。

    直接 ``b"".join(streaming_content)`` 会把整份产物读回内存，等于把流式白做了；
    这里边收边写盘，返回可直接交给 ``UploadFile`` 的文件对象。
    """
    import tempfile

    handle = tempfile.TemporaryFile()
    try:
        for chunk in response.streaming_content:
            handle.write(chunk)
    except Exception:
        handle.close()
        raise
    handle.seek(0)
    return handle


def build_export_request(record: Any, query_params: Any, user: Any) -> Any:
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


def _save_progress(record: Any, percent: Any, stage: Any = "") -> None:
    """运行中进度落库（0-100） 统一助手（里程碑即协作式取消的安全点）。

    终态由任务结束分支覆盖（终态 100 不做取消检查，避免已完成的导出被翻成取消）。
    """
    from task.utils.task_progress import KIND_EXPORT, update_progress

    update_progress(KIND_EXPORT, record.pk, percent, stage=stage)
    # 同步调用方内存对象（后续分支可能基于 record 继续 save）
    record.progress = max(0, min(100, int(percent)))


def _notify_export_result(record: Any, user: Any, state: Any, start_time: Any) -> None:
    """导出终态站内信（成功/失败/取消同一外壳）：失败只记日志，不影响任务结论。"""
    from common.notifications import ExportDataMessage

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


def run_async_export(record_id: Any, view_path: Any, query_params: Any, user_pk: Any) -> Any:
    """异步执行数据导出：重放 export_data 视图，产物落 UploadFile 供下载中心取用。

    记录状态在任务内推进（PENDING → RUNNING → SUCCESS/FAILURE）；同 pk 的
    TaskExecution 由 after_task_publish/prerun/postrun 信号自动记账，
    因此执行历史页与增量日志零成本复用。

    终态守卫：记录已是 SUCCESS/FAILURE/REVOKED 时直接返回（幂等），重复投递
    不会重复渲染、不会覆盖已有产物。主键即 task_id，同一任务在库层面只有一行。
    """
    from identity.models import UserInfo
    from task.models.export import ExportRecord
    from task.models.task import TaskExecution

    record = ExportRecord.objects.filter(pk=record_id).first()
    if record is None:
        logger.warning("Export record not found: %s", record_id)
        return 0
    if record.status in ExportRecord.TERMINAL_STATUSES:
        logger.info("async export skipped (record already %s): %s", record.status, record_id)
        return record.rows or 0
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
        if response.status_code != 200:
            raise ValueError(f"export view returned status {response.status_code}")
        # 同步导出走流式文件响应（FileResponse）：把流落到匿名临时文件，避免整包进内存
        content: Any
        if hasattr(response, "streaming_content"):
            content = _drain_streaming_response(response)
        else:
            response.render()
            content = response.content
        _save_progress(record, 80, stage=_("Rendering content"))

        filename = f"{record.name}.{record.file_format}"
        persist_export_artifact(record, filename, content, user=user)
        if hasattr(content, "close"):
            content.close()
        record.rows = min(total, getattr(settings, "EXPORT_MAX_LIMIT", total))
        record.status = ExportRecord.Status.SUCCESS
        record.progress = 100
        record.error = None
        record.save(update_fields=["file", "rows", "status", "progress", "error", "updated_time"])
        logger.info("async export done: rows=%s, file=%s", record.rows, filename)
    except TaskCancelled as exc:
        # 协作式取消：落 REVOKED 终态并同步执行历史行；不 re-raise（不是故障）
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
            _notify_export_result(record, user, state, start_time)
    return record.rows
