#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : file
# author : ly_13
# date : 7/24/2024

import hashlib
import os
import re
from urllib.parse import quote

from django.core.cache import cache
from django.http import FileResponse, HttpResponse
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser

from common.base.magic import cache_response
from common.core.config import SysConfig, UserConfig
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, RecycleBinAction
from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import UploadFile
from system.serializers.upload import UploadFileSerializer
from system.utils.preview import (
    KIND_IMAGE,
    KIND_OFFICE,
    KIND_PDF,
    KIND_TEXT,
    PREVIEW_STATUS_PREPARING,
    PREVIEW_STATUS_READY,
    SIZE_THUMB,
    ensure_image_cache,
    ensure_office_pdf,
    preview_kind,
    read_text_preview,
    touch_preview_cache,
)

logger = get_logger(__name__)

# 超出个人配额（存储/数量）的业务码：前端按该码提示配额不足
QUOTA_EXCEEDED_CODE = 1004
# 不支持在线预览的业务码：前端按该码禁用预览按钮并说明原因
PREVIEW_UNSUPPORTED_CODE = 1005
# Office 转换中的业务码（ADR-013）：前端稍后重试预览请求
PREVIEW_PREPARING_CODE = 1006


def get_upload_max_size(user_obj):
    return min(SysConfig.FILE_UPLOAD_SIZE, UserConfig(user_obj).FILE_UPLOAD_SIZE)


def _inline_file_response(path, content_type, filename):
    """inline 响应：浏览器直接渲染（PDF 内嵌 / 图片展示）而非下载。

    `Content-Disposition: inline` + RFC 5987 文件名编码，与下载口径同源
    （见 `views/admin/record_base.py` 的 attachment 版本）。
    """
    response = FileResponse(open(path, "rb"), as_attachment=False, content_type=content_type)
    response["Content-Disposition"] = "inline; filename*=UTF-8''{}".format(quote(filename or ""))
    response["Access-Control-Expose-Headers"] = "Content-Disposition, X-Preview-Truncated"
    return response


def sanitize_filename(name, max_length=255):
    """清洗客户端文件名：去除路径部分、控制字符与首尾空白，并限制长度。

    客户端提交的文件名不可信：可能携带路径分隔符（伪造存储路径）或控制字符。
    """
    if not name:
        return str(_("Unnamed file"))
    # 同时处理 POSIX(/) 与 Windows(\) 分隔符，防止路径穿越
    base = os.path.basename(str(name).replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    if not base or base in (".", ".."):
        return str(_("Unnamed file"))
    return base[:max_length]


def file_md5(file_obj) -> str:
    """计算上传文件的 md5（落盘前求值：命中去重时无需再写一份磁盘文件）。

    上传链路原先由 ``UploadFile.save()`` 读已落盘文件计算 md5；去重需要在落盘**之前**
    拿到内容指纹，故此处统一改为前置计算并显式入库（save() 见 md5sum 非空即跳过）。
    """
    digest = hashlib.md5()
    for chunk in file_obj.chunks():
        digest.update(chunk)
    return digest.hexdigest()


def find_dedup_source(creator, md5sum):
    """去重来源：同属主的既有活动上传件；跨用户不复用（避免越权复用他人文件的存储路径）。

    只认 ``is_upload=True`` 且未软删除的记录（回收站中的文件不参与复用），
    空 md5（异常文件）不复用。
    """
    if not md5sum:
        return None
    return UploadFile.objects.filter(creator=creator, md5sum=md5sum, is_upload=True).order_by("-created_time").first()


def invalidate_upload_stats_cache(user_pk):
    """失效个人文件统计短缓存（键口径与 get_stats_cache_key 一致）。

    上传成功后立刻刷新页面时，10s 短缓存会返回旧的使用率，故主动失效。
    """
    cache.delete(f"magic_cache_response_UploadFileViewSet_stats_{user_pk}")


class UploadFileFilter(BaseFilterSet):
    filename = filters.CharFilter(field_name="filename", lookup_expr="icontains")
    category = filters.CharFilter(field_name="category", lookup_expr="iexact")

    class Meta:
        model = UploadFile
        fields = ["filename", "category", "mime_type", "md5sum", "description", "is_upload", "is_tmp"]


class UploadFileViewSet(RecycleBinAction, BaseModelSet):
    """文件"""

    queryset = UploadFile.objects.all()
    serializer_class = UploadFileSerializer
    # 默认排序：分页器要求有序 queryset（否则抛 UnorderedObjectListWarning），
    # 且「最新上传在前」与文件管理页使用习惯一致（同 ImportRecord/ApprovalRequest 口径）
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "filesize"]
    filterset_class = UploadFileFilter

    # stats 短缓存：10s 内重复刷新不重复聚合；按用户区分缓存键
    def get_stats_cache_key(self, view_instance, view_method, request, args, kwargs):
        return f"{view_instance.__class__.__name__}_{view_method.__name__}_{request.user.pk}"

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "count": build_basic_type(OpenApiTypes.NUMBER),
                        "total_size": build_basic_type(OpenApiTypes.NUMBER),
                        "quota_mb": build_basic_type(OpenApiTypes.NUMBER),
                        "usage_rate": build_basic_type(OpenApiTypes.NUMBER),
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="stats")
    @cache_response(timeout=10, key_func="get_stats_cache_key")
    def stats(self, request, *args, **kwargs):
        """个人文件统计（数量/总大小/配额使用率）"""
        # 配额按上传人维度聚合（creator 索引），与管理页「我的文件」口径一致
        queryset = UploadFile.objects.filter(creator=request.user)
        count = queryset.count()
        total_size = queryset.aggregate(size=Sum("filesize"))["size"] or 0
        quota_mb = SysConfig.FILE_STORAGE_QUOTA_MB or 0
        quota_bytes = quota_mb * 1024 * 1024
        usage_rate = round(total_size / quota_bytes * 100, 2) if quota_bytes else 0
        return ApiResponse(
            data={"count": count, "total_size": total_size, "quota_mb": quota_mb, "usage_rate": usage_rate}
        )

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "file_upload_size": build_basic_type(OpenApiTypes.NUMBER),
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False)
    def config(self, request, *args, **kwargs):
        """获取上传配置"""
        return ApiResponse(data={"file_upload_size": get_upload_max_size(request.user)})

    @action(methods=["get"], detail=True, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """在线预览：走 DRF 鉴权与数据权限（不暴露 /media/ 直链）。

        类型分派由后端单一判定（序列化器同步下发 `preview_kind`）：
        - 图片：`?size=thumb|preview`，按需生成 JPEG 缓存后 `inline` 返回；
        - PDF：`inline` 流式返回，由浏览器内嵌渲染；
        - 文本：按 `FILE_PREVIEW_TEXT_MAX_BYTES` 截断，以 `text/plain` 返回，
          截断状态放在 `X-Preview-Truncated` 响应头（前端据此提示"过大，请下载"）；
        - Office（docx/xlsx/pptx 等，ADR-013）：LibreOffice 转 PDF 后内嵌渲染，
          转换在 heavy 队列执行；产物未就绪返回业务码 1006（前端稍后重试），
          转换器缺失/超限/关闭时降级为 1005；
        - 其余类型：返回业务码 1005（前端按 `preview_kind` 已提前禁用按钮）。
        """
        upload = self.get_object()
        kind = preview_kind(upload)
        if kind is None or not upload.filepath:
            return ApiResponse(
                code=PREVIEW_UNSUPPORTED_CODE,
                detail=_("This file type does not support preview"),
            )

        if kind == KIND_TEXT:
            content, truncated = read_text_preview(upload)
            if not content:
                return ApiResponse(
                    code=PREVIEW_UNSUPPORTED_CODE,
                    detail=_("This file type does not support preview"),
                )
            response = HttpResponse(content, content_type="text/plain; charset=utf-8")
            response["X-Preview-Truncated"] = "1" if truncated else "0"
            return response

        if kind == KIND_IMAGE:
            size = request.query_params.get("size") or SIZE_THUMB
            cache_path = ensure_image_cache(upload, size)
            if not cache_path:
                return ApiResponse(
                    code=PREVIEW_UNSUPPORTED_CODE,
                    detail=_("This file type does not support preview"),
                )
            touch_preview_cache(cache_path)
            return _inline_file_response(cache_path, "image/jpeg", upload.filename)

        # PDF：原样 inline 返回（浏览器内嵌渲染，不生成缓存）
        if kind == KIND_PDF:
            path = upload.filepath.path
            if not os.path.exists(path):
                return ApiResponse(code=1001, detail=_("File not found"))
            return _inline_file_response(path, upload.mime_type or "application/pdf", upload.filename)

        # Office：转换产物就绪即 inline 返回；转换中回 1006 由前端重试
        if kind == KIND_OFFICE:
            path, status = ensure_office_pdf(upload)
            if status == PREVIEW_STATUS_READY and path:
                touch_preview_cache(path)
                return _inline_file_response(path, "application/pdf", upload.filename)
            if status == PREVIEW_STATUS_PREPARING:
                # 425 Too Early：前端按「转换中」轮询重试（http 层对该状态码不弹全局错误）
                return ApiResponse(
                    code=PREVIEW_PREPARING_CODE,
                    status=425,
                    detail=_("The document is being converted, please try again later"),
                    data={"status": "preparing"},
                )

        return ApiResponse(
            code=PREVIEW_UNSUPPORTED_CODE,
            detail=_("This file type does not support preview"),
        )

    @extend_schema(
        description="文件上传",
        request=OpenApiRequest(
            build_object_type(properties={"file": build_array_type(build_basic_type(OpenApiTypes.BINARY))})
        ),
        responses={
            200: inline_serializer(
                name="result",
                fields={
                    "code": serializers.IntegerField(),
                    "detail": serializers.CharField(),
                    "data": UploadFileSerializer(many=True),
                },
            )
        },
    )
    @action(
        methods=["post"],
        detail=False,
        throttle_classes=[
            UploadThrottle,
        ],
        parser_classes=(MultiPartParser,),
    )
    def upload(self, request, *args, **kwargs):
        """上传文件"""

        files = request.FILES.getlist("file", [])
        result = []
        file_upload_max_size = get_upload_max_size(request.user)
        # 配额校验前置到落盘之前（超额 1004 且不落盘）；仅有限额配置时才查聚合
        quota_mb = SysConfig.FILE_STORAGE_QUOTA_MB or 0
        count_limit = SysConfig.FILE_UPLOAD_COUNT_LIMIT or 0
        owner_files = UploadFile.objects.filter(creator=request.user)
        used_size = (owner_files.aggregate(size=Sum("filesize"))["size"] or 0) if quota_mb else 0
        used_count = owner_files.count() if count_limit else 0
        # 先全量校验再统一落库：任一文件不合规直接返回错误，避免多文件上传时
        # 「前面的已落库、后面的被拒」造成部分写入
        for file_obj in files:
            try:
                file_size = file_obj.size
            except Exception as e:
                logger.error(f"user:{request.user} upload file type error Exception:{e}")
                return ApiResponse(code=1002, detail=_("Wrong upload file type"))
            if file_size > file_upload_max_size:
                return ApiResponse(
                    code=1003, detail=_("upload file size cannot exceed {}").format(file_upload_max_size)
                )
            if quota_mb and used_size + file_size > quota_mb * 1024 * 1024:
                return ApiResponse(
                    code=QUOTA_EXCEEDED_CODE,
                    detail=_("Storage quota exceeded ({} MB), please clean up and retry").format(quota_mb),
                )
            if count_limit and used_count + 1 > count_limit:
                return ApiResponse(
                    code=QUOTA_EXCEEDED_CODE,
                    detail=_("File count limit exceeded ({}), please clean up and retry").format(count_limit),
                )
            used_size += file_size
            used_count += 1
        # 统一落库：整批包在同一事务内，任一文件写入失败则整体回滚，
        # 避免「前面的已落库、后面的失败」造成部分写入（与前置全量校验配套）
        dedup_hits = 0
        try:
            with transaction.atomic():
                for file_obj in files:
                    filename = sanitize_filename(file_obj.name)
                    # md5 在落盘前求值：命中去重时不能再写一份磁盘文件
                    md5sum = file_md5(file_obj)
                    source = find_dedup_source(request.user, md5sum)
                    if source:
                        # 去重命中：复用既有物理文件，仅新建引用记录（不落盘）。
                        # 归属语义不受影响（新记录仍是本次属主的上传件），删除任一记录时
                        # 物理文件由 UploadFile.file_still_referenced 守护保留
                        dedup_hits += 1
                        result.append(
                            UploadFile.objects.create(
                                creator=request.user,
                                filename=filename,
                                is_upload=True,
                                is_tmp=True,
                                filepath=source.filepath.name,
                                mime_type=file_obj.content_type,
                                filesize=file_obj.size,
                                md5sum=md5sum,
                            )
                        )
                        continue
                    result.append(
                        UploadFile.objects.create(
                            creator=request.user,
                            # 客户端原始文件名不可信：去掉路径部分并做非法字符/长度清洗
                            filename=filename,
                            is_upload=True,
                            is_tmp=True,
                            filepath=file_obj,
                            mime_type=file_obj.content_type,
                            filesize=file_obj.size,
                            md5sum=md5sum,
                        )
                    )
        except Exception as e:
            logger.exception(f"user:{request.user} upload file save failed: {e}")
            return ApiResponse(code=1002, detail=_("Failed to save uploaded file"))
        if result:
            # 配额使用率卡片依赖 stats 短缓存，上传后主动失效避免读到旧值
            invalidate_upload_stats_cache(request.user.pk)
        detail = _("Upload successful")
        if dedup_hits:
            detail = _("Upload successful, {} file(s) reused existing copies").format(dedup_hits)
        return ApiResponse(data=self.get_serializer(result, many=True).data, detail=detail)
