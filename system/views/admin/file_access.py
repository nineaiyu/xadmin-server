#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件访问审计与上传配置端点。

自 ``file.py`` 拆出（仅因行数门禁）：URL 路径、权限点、响应结构完全一致。
包含：下载（受鉴权，替代 /media/ 直链）、访问记录、删除留痕、上传配置下发。
"""

import os
from urllib.parse import quote

from django.db.models import Count
from django.http import FileResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.storage import storage_exists, storage_open, storage_presigned_url
from common.storage.utils import PRESIGN_DEFAULT_EXPIRES
from common.swagger.utils import get_default_response_schema
from system.models import FileAccessLog
from system.serializers.security import FileAccessLogSerializer
from system.utils.file_audit import get_upload_policy, log_file_access


def inline_file_response(source, content_type, filename):
    """inline 响应：浏览器直接渲染（PDF 内嵌 / 图片展示）而非下载。

    `source` 支持本地路径与已打开的文件对象（存储适配本地 / 对象存储统一入口）。
    拆分自 `file.py`（行数门禁），行为与下载口径同源（见 `record_base.py` 的 attachment 版本）。
    """
    file_obj = open(source, "rb") if isinstance(source, (str, os.PathLike)) else source
    response = FileResponse(file_obj, as_attachment=False, content_type=content_type)
    response["Content-Disposition"] = "inline; filename*=UTF-8''{}".format(quote(filename or ""))
    response["Access-Control-Expose-Headers"] = "Content-Disposition, X-Preview-Truncated"
    return response


class FileAccessActionMixin:
    """下载 / 访问记录 / 删除留痕 / 上传配置（self 由组合它的 ViewSet 提供）。"""

    def perform_destroy(self, instance):
        # 文件访问审计：删除留痕（批量删除逐行走本方法）
        log_file_access(
            upload=instance,
            user=getattr(self.request, "user", None),
            action=FileAccessLog.Action.DELETE,
            request=self.request,
            filename=instance.filename,
        )
        return super().perform_destroy(instance)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="download")
    def download(self, request, *args, **kwargs):
        """下载文件（DRF 鉴权 + 数据权限 + 访问审计，替代 /media/ 直链）

        ``?direct=1``（预签名直连）：s3 后端且 boto3 可用时返回短时效预签名 URL
        （大文件不经服务端中转）；鉴权与审计先于签发完成，不可用时返回 ``direct=false``
        由调用方回退服务端中转下载（本地 / mirror 后端恒为回退，行为零变化）。
        """
        upload = self.get_object()
        # 存储适配：本地 / 对象存储统一走 storage 原语，不再依赖本地绝对路径
        name = getattr(upload.filepath, "name", "") if upload.filepath else ""
        if not name or not storage_exists(name):
            log_file_access(
                upload=upload,
                user=request.user,
                action=FileAccessLog.Action.DOWNLOAD,
                request=request,
                result=False,
                detail="file missing",
            )
            return ApiResponse(code=1001, detail=_("File not found"))
        log_file_access(upload=upload, user=request.user, action=FileAccessLog.Action.DOWNLOAD, request=request)
        if str(request.query_params.get("direct") or "").lower() in ("1", "true", "yes"):
            url = storage_presigned_url(name, download_filename=upload.filename or "")
            return ApiResponse(
                data={"direct": bool(url), "url": url or "", "expires": PRESIGN_DEFAULT_EXPIRES if url else 0}
            )
        response = FileResponse(
            storage_open(name, "rb"), as_attachment=True, content_type=upload.mime_type or "application/octet-stream"
        )
        response["Content-Disposition"] = "attachment; filename*=UTF-8''{}".format(quote(upload.filename or ""))
        return response

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="access-logs")
    def access_logs(self, request, *args, **kwargs):
        """文件访问记录：最近 100 条 + 各动作计数"""
        upload = self.get_object()
        queryset = FileAccessLog.objects.filter(file=upload).order_by("-created_time")
        counts = {row["action"]: row["count"] for row in queryset.values("action").annotate(count=Count("pk"))}
        return ApiResponse(
            data={
                "results": FileAccessLogSerializer(queryset[:100], many=True).data,
                "total": queryset.count(),
                "counts": counts,
            }
        )

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "file_upload_size": build_basic_type(OpenApiTypes.NUMBER),
                        "upload_policy": build_object_type(),
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False)
    def config(self, request, *args, **kwargs):
        """获取上传配置"""
        # 延迟导入：get_upload_max_size 定义在主视图模块（避免模块级循环导入）
        from system.views.admin.file import get_upload_max_size

        return ApiResponse(
            data={
                "file_upload_size": get_upload_max_size(request.user),
                # 上传策略下发（前端在选择文件后即时提示，避免上传后才被拒）
                "upload_policy": get_upload_policy(),
            }
        )
