#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : file
# author : ly_13
# date : 7/24/2024

from django.db import transaction
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser

from common.base.magic import cache_response
from common.core.filter import BaseFilterSet, ControlledLookupFilterBackend
from common.core.modelset import BaseModelSet, RecycleBinAction
from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from file.models import FileAccessLog, UploadFile
from file.serializers.upload import UploadFileSerializer
from file.services.file_impl import (
    PREVIEW_PREPARING_CODE,
    PREVIEW_STATE_FILE_MISSING,
    PREVIEW_STATE_IMAGE,
    PREVIEW_STATE_OFFICE_PREPARING,
    PREVIEW_STATE_OFFICE_READY,
    PREVIEW_STATE_PDF,
    PREVIEW_STATE_TEXT,
    PREVIEW_UNSUPPORTED_CODE,
    build_personal_file_stats,
    resolve_preview,
)
from file.utils.file_audit import log_file_access
from file.utils.preview import preview_kind
from file.utils.upload_store import (
    INVALID_CODE,
    UploadError,
    check_upload_limits,
    invalidate_upload_stats_cache,
    store_upload_file,
)
from file.views.admin.file_access import FileAccessActionMixin, inline_file_response
from file.views.admin.file_chunk import ChunkUploadActionMixin
from system.utils.platform.tags import TagChoiceFilter, TagFilterBackend, TagFilterMixin, TaggedPrefetchMixin

logger = get_logger(__name__)


# 上传落库内核（扩展名/大小/配额校验、md5 去重、分类、存储）见
# file/utils/upload_store.py：聊天室附件等业务上传入口复用同一套安全策略，
# 避免两处规则各自演化；本模块的 upload / stats 响应口径不变。
# 统计聚合与预览状态机见 file/services/file_impl.py：本模块只保留鉴权、审计与响应构造。


class UploadFileFilter(TagFilterMixin, BaseFilterSet):
    filename = filters.CharFilter(field_name="filename", lookup_expr="icontains")
    category = filters.CharFilter(field_name="category", lookup_expr="iexact")
    tag = TagChoiceFilter()

    class Meta:
        model = UploadFile
        fields = ["filename", "category", "mime_type", "md5sum", "description", "is_upload", "is_tmp", "tag"]


class UploadFileViewSet(
    FileAccessActionMixin, ChunkUploadActionMixin, TaggedPrefetchMixin, RecycleBinAction, BaseModelSet
):
    """文件（含分片上传 / 断点续传：chunk/* 子动作见 file_chunk.py）"""

    queryset = UploadFile.objects.all()
    serializer_class = UploadFileSerializer
    # 默认排序：分页器要求有序 queryset（否则抛 UnorderedObjectListWarning），
    # 且「最新上传在前」与文件管理页使用习惯一致（同 ImportRecord/ApprovalRequest 口径）
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "filesize"]
    filterset_class = UploadFileFilter
    # 通用标签：?tag=<标签名> 过滤（预取走 TaggedPrefetchMixin）
    # 受控 lookup 透传：字段面 = UploadFileFilter 已声明字段（字段可见性 fail-closed）
    controlled_lookup = True
    extra_filter_class = [TagFilterBackend, ControlledLookupFilterBackend]

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
                        "remaining_size": build_basic_type(OpenApiTypes.NUMBER),
                        "avg_size": build_basic_type(OpenApiTypes.NUMBER),
                        "category_stats": build_array_type(
                            build_object_type(
                                properties={
                                    "value": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                    "color": build_basic_type(OpenApiTypes.STR),
                                    "count": build_basic_type(OpenApiTypes.NUMBER),
                                    "size": build_basic_type(OpenApiTypes.NUMBER),
                                }
                            )
                        ),
                        "recent_trend": build_array_type(
                            build_object_type(
                                properties={
                                    "date": build_basic_type(OpenApiTypes.STR),
                                    "count": build_basic_type(OpenApiTypes.NUMBER),
                                    "size": build_basic_type(OpenApiTypes.NUMBER),
                                }
                            )
                        ),
                        "top_files": build_array_type(
                            build_object_type(
                                properties={
                                    "pk": build_basic_type(OpenApiTypes.STR),
                                    "filename": build_basic_type(OpenApiTypes.STR),
                                    "filesize": build_basic_type(OpenApiTypes.NUMBER),
                                }
                            )
                        ),
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="stats")
    @cache_response(timeout=10, key_func="get_stats_cache_key")
    def stats(self, request, *args, **kwargs):
        """个人文件统计（数量/总大小/配额使用率 + 分类分布/近 7 天趋势/最大文件）。

        顶部统计面板的数据源：列表口径（活动记录）+ 一次聚合出多组维度，
        由 10s 短缓存兜住重复刷新；`?no_cache=1` 可穿透缓存取即时值。
        聚合口径见 system/services/file.py（build_personal_file_stats）。
        """
        return ApiResponse(data=build_personal_file_stats(request.user))

    @action(methods=["get"], detail=True, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """在线预览：走 DRF 鉴权与数据权限（不暴露 /media/ 直链）。

        类型分派由后端单一判定（序列化器同步下发 `preview_kind`）：
        - 图片：`?size=thumb|preview`，按需生成 JPEG 缓存后 `inline` 返回；
        - PDF：`inline` 流式返回，由浏览器内嵌渲染；
        - 文本：按 `FILE_PREVIEW_TEXT_MAX_BYTES` 截断，以 `text/plain` 返回，
          截断状态放在 `X-Preview-Truncated` 响应头（前端据此提示"过大，请下载"）；
        - Office（docx/xlsx/pptx 等）：LibreOffice 转 PDF 后内嵌渲染，
          转换在 heavy 队列执行；产物未就绪返回业务码 1006（前端稍后重试），
          转换器缺失/超限/关闭时降级为 1005；
        - 其余类型：返回业务码 1005（前端按 `preview_kind` 已提前禁用按钮）。

        状态判定见 system/services/file.py（resolve_preview），本方法只做
        状态 → 响应的映射。
        """
        upload = self.get_object()
        kind = preview_kind(upload)
        # 文件访问审计：预览留痕（类型进 detail，便于按访问方式统计）
        log_file_access(
            upload=upload,
            user=request.user,
            action=FileAccessLog.Action.PREVIEW,
            request=request,
            detail=f"kind={kind or ''}",
        )
        state, payload = resolve_preview(upload, kind, request.query_params.get("size"))
        if state == PREVIEW_STATE_FILE_MISSING:
            return ApiResponse(code=1001, detail=_("File not found"))
        if state == PREVIEW_STATE_TEXT:
            content, truncated = payload
            response = HttpResponse(content, content_type="text/plain; charset=utf-8")
            response["X-Preview-Truncated"] = "1" if truncated else "0"
            return response
        if state == PREVIEW_STATE_IMAGE:
            return inline_file_response(payload, "image/jpeg", upload.filename)
        if state == PREVIEW_STATE_PDF:
            stream, mime_type = payload
            return inline_file_response(stream, mime_type, upload.filename)
        if state == PREVIEW_STATE_OFFICE_READY:
            return inline_file_response(payload, "application/pdf", upload.filename)
        if state == PREVIEW_STATE_OFFICE_PREPARING:
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
            build_object_type(properties={"file": build_array_type(build_basic_type(OpenApiTypes.BINARY) or {})})
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
        # 先全量校验再统一落库（内核见 file/utils/upload_store.py）：任一文件不合规
        # 直接返回错误（1002/1003/1004 且不落盘），避免多文件上传时「前面的已落库、
        # 后面的被拒」造成部分写入
        try:
            check_upload_limits(request.user, files)
        except UploadError as exc:
            return ApiResponse(code=exc.code, detail=exc.detail)
        # 统一落库：整批包在同一事务内，任一文件写入失败则整体回滚（与前置全量校验配套）
        result = []
        dedup_hits = 0
        try:
            with transaction.atomic():
                for file_obj in files:
                    upload, dedup_hit = store_upload_file(request.user, file_obj)
                    dedup_hits += 1 if dedup_hit else 0
                    result.append(upload)
        except Exception as e:
            logger.exception(f"user:{request.user} upload file save failed: {e}")
            return ApiResponse(code=INVALID_CODE, detail=_("Failed to save uploaded file"))
        if result:
            # 配额使用率卡片依赖 stats 短缓存，上传后主动失效避免读到旧值
            invalidate_upload_stats_cache(request.user.pk)
            # 文件访问审计：上传留痕（含去重命中的引用记录）
            for upload in result:
                log_file_access(upload=upload, user=request.user, action=FileAccessLog.Action.UPLOAD, request=request)
        detail = _("Upload successful")
        if dedup_hits:
            detail = _("Upload successful, {} file(s) reused existing copies").format(dedup_hits)
        return ApiResponse(data=self.get_serializer(result, many=True).data, detail=detail)
