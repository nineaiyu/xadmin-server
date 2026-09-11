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
from rest_framework.decorators import action
from rest_framework.filters import BaseFilterBackend

from common.base.magic import cache_response
from common.core.response import ApiResponse
from system.utils.record_stats import (
    RECORD_STATS_CACHE_SECONDS,
    record_stats,
)
from system.utils.task_log import read_task_log_chunk


class RecordStatsMixin:
    """记录类视图的统计 action 公共实现（导出 / 导入 / 任务执行）。

    口径与缓存键集中在此，避免三处各自实现后漂移：
    - 统计口径走 `system.utils.record_stats.record_stats` 纯函数；
    - 10s 短缓存（与审批 pending-count 同范式），`?no_cache=1` 旁路由
      `MagicCacheResponse` 内建，无需各视图重复实现。
    """

    #: 传给 `record_stats` 的差异项（默认适用于 PENDING/RUNNING + FAILURE/REVOKED/FAILED）
    record_stats_kwargs: dict = {}

    def get_stats_cache_key(self, view_instance, view_method, request, args, kwargs):
        return f"{self.__class__.__name__}_{view_method.__name__}_{request.user.pk}"

    @action(methods=["get"], detail=False)
    @cache_response(timeout=RECORD_STATS_CACHE_SECONDS, key_func="get_stats_cache_key")
    def stats(self, request, *args, **kwargs):
        """近 N 天记录统计（总数 / 进行中 / 失败 / 最近一次），按「我的」收口。"""
        model = self.queryset.model
        return ApiResponse(data=record_stats(model.objects.all(), request.user, **self.record_stats_kwargs))


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
