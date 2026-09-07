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
    # SEC-5：扩展名可伪造，按文件头魔数做二次校验；扩展新类型时需同步补充签名
    FILE_UPLOAD_MAGIC = {
        "png": b"\x89PNG\r\n\x1a\n",
        "jpeg": b"\xff\xd8\xff",
        "jpg": b"\xff\xd8\xff",
        "gif": b"GIF8",  # GIF87a / GIF89a 公共前缀
    }
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
        if not files:
            return ApiResponse(code=1002, detail=_("Please select the file to upload"))
        instance = self.get_object()
        file_obj = files[0]
        wrong_type_detail = _("Wrong image type, the type should be {}").format(",".join(self.FILE_UPLOAD_TYPE))
        file_type = file_obj.name.split(".")[-1].lower()
        magic = self.FILE_UPLOAD_MAGIC.get(file_type)
        if magic is None or not file_obj.read(len(magic)).startswith(magic):
            return ApiResponse(code=1002, detail=wrong_type_detail)
        file_obj.seek(0)  # 魔数读取移动了文件指针，复位后再交给存储后端，避免保存被截断的内容
        if file_obj.size > self.FILE_UPLOAD_SIZE:
            return ApiResponse(code=1003, detail=_("Image size cannot exceed {}").format(self.FILE_UPLOAD_SIZE))
        setattr(instance, self.FILE_UPLOAD_FIELD, file_obj)
        instance.modifier = request.user
        instance.save(update_fields=[self.FILE_UPLOAD_FIELD, "modifier"])
        return ApiResponse()
