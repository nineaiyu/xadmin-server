#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""下载中心（导入/导出记录）的公共实现。

导入记录与导出记录两个 ViewSet 的取值域过滤、文件下载（DRF 鉴权而非 /media/ 直出）、
任务日志增量读取三处逻辑原本逐字重复；此处收敛为共享实现，子类只保留模型差异
（文件字段名、文案、终态集合）与各自的 OpenAPI schema 声明。
"""

import os
from urllib.parse import quote

from django.http import FileResponse
from django.utils.translation import gettext_lazy as _
from rest_framework.filters import BaseFilterBackend

from common.core.response import ApiResponse
from system.utils.task_log import read_task_log_chunk


class RecordOwnerFilter(BaseFilterBackend):
    """下载中心取值域：超管全部，普通用户仅本人提交的记录。

    不走通用数据权限（默认拒绝会让普通用户看不到自己提交的记录）。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        if user.is_superuser:
            return queryset
        return queryset.filter(creator=user)


class RecordFileDownloadMixin:
    """记录关联文件下载的公共实现（经 DRF 鉴权，避免拿到 URL 即可下载敏感文件）。

    子类需声明 ``download_file_field``（记录上指向 UploadFile 的字段名）与
    ``download_not_found_message``（缺文件时的业务提示文案）。
    """

    download_file_field = ""
    download_not_found_message = _("File not found")

    def download_upload_file(self, upload):
        """文件缺失返回可读业务错误，否则返回 FileResponse。"""
        if not upload or not upload.filepath:
            return ApiResponse(code=1001, detail=self.download_not_found_message)
        path = upload.filepath.path
        if not os.path.exists(path):
            return ApiResponse(code=1001, detail=self.download_not_found_message)
        response = FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename=upload.filename,
            content_type=upload.mime_type or "application/octet-stream",
        )
        # RFC 5987：中文文件名需 UTF-8 编码声明，否则浏览器解出乱码
        response["Content-Disposition"] = "attachment; filename*=UTF-8''{}".format(quote(upload.filename))
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response

    def download_record_file(self):
        """取当前对象上声明的文件字段并下载。"""
        record = self.get_object()
        return self.download_upload_file(getattr(record, self.download_file_field, None))


class RecordTaskLogMixin:
    """记录任务日志增量读取的公共实现。

    子类需声明 ``log_finished_statuses``（视为终态、无需再轮询的状态集合）。
    """

    log_finished_statuses = ()

    def read_record_task_log(self, request):
        record = self.get_object()
        data = read_task_log_chunk(
            record.pk,
            offset=request.query_params.get("offset") or 0,
            finished_hint=record.status in self.log_finished_statuses,
        )
        return ApiResponse(data=data)
