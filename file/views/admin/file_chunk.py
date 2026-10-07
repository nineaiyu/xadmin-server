#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""分片上传 / 断点续传端点（自 file.py 拆出，仅因行数门禁）。

四个子 action 全部以 ``parent_fallback_action`` 声明：权限优先按自身权限点
（如角色显式绑定 ``api/system/file/chunk/init$``），未绑定时回退父级
list / create 口径——既有可使用文件中心的角色无需重新授权即可获得分片能力。
协议与安全策略见 :mod:`file.utils.upload_chunk`（与单请求上传同源）。
"""

from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser

from common.core.permission_meta import parent_fallback_action
from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from common.utils import get_logger
from file.models import FileAccessLog, UploadSession
from file.utils.file_audit import log_file_access
from file.utils.upload_chunk import (
    CHECKSUM_MISMATCH_CODE,
    DEFAULT_CHUNK_SIZE,
    abort_session,
    complete_session,
    init_session,
    store_part,
)
from file.utils.upload_store import UploadError, invalidate_upload_stats_cache

logger = get_logger(__name__)

# 会话不存在 / 非本人的业务码：与其它 1001 形态的可读错误一致
SESSION_MISSING_CODE = 1001


def _parse_session_pk(raw):
    """会话主键统一收敛为 int（畸形字符串返回 None，由调用方回 1001 而非 500）。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class ChunkUploadActionMixin:
    """挂到 UploadFileViewSet 的分片上传动作组（url 前缀 chunk/*）。

    宿主为 BaseModelSet 系视图集（运行期提供 ``get_serializer``）；
    TYPE_CHECKING 块仅为 mypy 声明该契约面，不参与运行期。
    """

    if TYPE_CHECKING:

        def get_serializer(self, *args: Any, **kwargs: Any) -> serializers.Serializer: ...

    @extend_schema(
        description="分片上传：创建或命中（断点续传）会话",
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "filename": build_basic_type(OpenApiTypes.STR),
                    "filesize": build_basic_type(OpenApiTypes.NUMBER),
                    "total_chunks": build_basic_type(OpenApiTypes.NUMBER),
                    "chunk_size": build_basic_type(OpenApiTypes.NUMBER),
                    "md5": build_basic_type(OpenApiTypes.STR),
                },
                required=["filename", "filesize", "total_chunks", "chunk_size"],
            )
        ),
        responses={
            200: inline_serializer(
                name="chunkInitResult",
                fields={
                    "session": serializers.CharField(),
                    "chunk_size": serializers.IntegerField(),
                    "received": build_array_type(build_basic_type(OpenApiTypes.NUMBER) or {}),
                    "created": serializers.BooleanField(),
                },
            )
        },
    )
    @parent_fallback_action(methods=["post"], detail=False, throttle_classes=[UploadThrottle], url_path="chunk/init")
    def chunk_init(self, request, *args, **kwargs):
        data = request.data or {}
        try:
            session, received, created = init_session(
                request.user,
                filename=data.get("filename") or "",
                filesize=data.get("filesize") or 0,
                total_chunks=data.get("total_chunks") or 0,
                chunk_size=data.get("chunk_size") or DEFAULT_CHUNK_SIZE,
                md5sum=data.get("md5") or "",
                mime_type=data.get("mime_type") or "",
            )
        except UploadError as exc:
            return ApiResponse(code=exc.code, detail=exc.detail)
        return ApiResponse(
            data={
                "session": str(session.pk),
                "chunk_size": session.chunk_size,
                "received": received,
                "created": created,
            },
            detail=_("Upload session created") if created else _("Existing upload session resumed"),
        )

    @extend_schema(
        description="分片上传：传输单个分片（幂等，重传覆盖）",
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "session": build_basic_type(OpenApiTypes.STR),
                    "index": build_basic_type(OpenApiTypes.NUMBER),
                    "file": build_object_type(properties={"": build_basic_type(OpenApiTypes.BINARY) or {}}),
                },
                required=["session", "index", "file"],
            )
        ),
        responses={
            200: inline_serializer(
                name="chunkPartResult",
                fields={"received_count": serializers.IntegerField()},
            )
        },
    )
    @parent_fallback_action(
        methods=["post"],
        detail=False,
        throttle_classes=[UploadThrottle],
        parser_classes=[MultiPartParser],
        url_path="chunk/part",
    )
    def chunk_part(self, request, *args, **kwargs):
        session_pk = _parse_session_pk(request.data.get("session"))
        index = request.data.get("index")
        file_obj = request.FILES.get("file")
        if session_pk is None or index is None or file_obj is None:
            return ApiResponse(code=SESSION_MISSING_CODE, detail=_("Missing session / index / file"))
        # 会话查询带 creator 收敛取值域：非本人会话走同一条「会话不存在」路径
        # （store_part 对他人会话也是同样的拒绝口径），少一次注定被拒的行读取
        session = UploadSession.objects.filter(pk=session_pk, creator=request.user).first()
        if not session:
            return ApiResponse(code=SESSION_MISSING_CODE, detail=_("Invalid upload session"))
        try:
            received_count = store_part(request.user, session, index, file_obj)
        except UploadError as exc:
            return ApiResponse(code=exc.code, detail=exc.detail)
        return ApiResponse(data={"received_count": received_count}, detail=_("Chunk uploaded"))

    @extend_schema(
        description="分片上传：合并分片并落库（断点传输收口）",
        request=OpenApiRequest(
            build_object_type(
                properties={"pk": build_basic_type(OpenApiTypes.STR), "md5": build_basic_type(OpenApiTypes.STR)},
                required=["pk"],
            )
        ),
    )
    @parent_fallback_action(
        methods=["post"], detail=False, throttle_classes=[UploadThrottle], url_path="chunk/complete"
    )
    def chunk_complete(self, request, *args, **kwargs):
        data = request.data or {}
        session_pk = _parse_session_pk(data.get("pk"))
        if session_pk is None:
            return ApiResponse(code=SESSION_MISSING_CODE, detail=_("Missing session"))
        if not UploadSession.objects.filter(pk=session_pk).exists():
            return ApiResponse(code=SESSION_MISSING_CODE, detail=_("Invalid upload session"))
        try:
            upload, session = complete_session(request.user, session_pk, declared_md5=data.get("md5") or "")
        except UploadError as exc:
            if exc.code == CHECKSUM_MISMATCH_CODE:
                # 声明指纹与拼合结果不一致：大概率分片损坏/串位，留证据供排查
                logger.warning(f"user:{request.user} chunk upload checksum mismatch: {session_pk}")
            return ApiResponse(code=exc.code, detail=exc.detail)
        # 与单请求 upload 同口径：stats 短缓存失效 + 上传留痕
        invalidate_upload_stats_cache(request.user.pk)
        log_file_access(upload=upload, user=request.user, action=FileAccessLog.Action.UPLOAD, request=request)
        return ApiResponse(data=self.get_serializer(upload).data, detail=_("Upload successful"))

    @extend_schema(
        description="分片上传：放弃会话并清理分片",
        request=OpenApiRequest(
            build_object_type(properties={"pk": build_basic_type(OpenApiTypes.STR)}, required=["pk"])
        ),
    )
    @parent_fallback_action(methods=["post"], detail=False, throttle_classes=[UploadThrottle], url_path="chunk/abort")
    def chunk_abort(self, request, *args, **kwargs):
        session_pk = _parse_session_pk((request.data or {}).get("pk"))
        if session_pk is None:
            return ApiResponse(code=SESSION_MISSING_CODE, detail=_("Missing session"))
        aborted = abort_session(request.user, session_pk)
        return ApiResponse(detail=_("Upload session aborted") if aborted else _("Invalid upload session"))
