#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件上传 Action：单文件（头像/封面）上传端点。

各视图按需混入（BaseModelSet 默认不包含）。拆分自 modelset.py（T2.1）。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser

from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema


class UploadFileAction(object):
    FILE_UPLOAD_TYPE = ["png", "jpeg", "jpg", "gif"]
    FILE_UPLOAD_FIELD = "avatar"
    FILE_UPLOAD_SIZE = settings.FILE_UPLOAD_SIZE

    def get_upload_size(self):
        return SysConfig.PICTURE_UPLOAD_SIZE

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"file": build_basic_type(OpenApiTypes.BINARY)})),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, parser_classes=(MultiPartParser,))
    def upload(self, request, *args, **kwargs):
        """上传头像"""
        self.FILE_UPLOAD_SIZE = self.get_upload_size()
        files = request.FILES.getlist("file", [])
        instance = self.get_object()
        file_obj = files[0]
        try:
            file_type = file_obj.name.split(".")[-1]
            if file_type not in self.FILE_UPLOAD_TYPE:
                raise
            if file_obj.size > self.FILE_UPLOAD_SIZE:
                return ApiResponse(code=1003, detail=_("Image size cannot exceed {}").format(self.FILE_UPLOAD_SIZE))
        except Exception:
            return ApiResponse(
                code=1002, detail=_("Wrong image type, the type should be {}").format(",".join(self.FILE_UPLOAD_TYPE))
            )
        setattr(instance, self.FILE_UPLOAD_FIELD, file_obj)
        instance.modifier = request.user
        instance.save(update_fields=[self.FILE_UPLOAD_FIELD, "modifier"])
        return ApiResponse()
