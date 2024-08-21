#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : upload
# author : ly_13
# date : 6/26/2023
import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest, extend_schema_view, inline_serializer
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import MultiPartParser

from common.core.config import SysConfig, UserConfig
from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from common.swagger.utils import get_default_response_schema
from system.models import UploadFile
from system.serializers.upload import UploadFileSerializer

logger = logging.getLogger(__file__)


def get_upload_max_size(user_obj):
    return min(SysConfig.FILE_UPLOAD_SIZE, UserConfig(user_obj).FILE_UPLOAD_SIZE)


@extend_schema_view(
    post=extend_schema(
        description="文件上传",
        request=OpenApiRequest(
            build_object_type(properties={'file': build_array_type(build_basic_type(OpenApiTypes.BINARY))})
        ),
        responses={
            200: inline_serializer(name='result', fields={
                'code': serializers.IntegerField(),
                'detail': serializers.CharField(),
                'data': UploadFileSerializer(many=True)
            })
        }
    ),
    get=extend_schema(
        responses=get_default_response_schema({
            'data': build_object_type(
                properties={
                    'file_upload_size': build_basic_type(OpenApiTypes.NUMBER),
                }
            )
        })
    )
)
class UploadView(GenericAPIView):
    """本地上传文件接口"""
    throttle_classes = [UploadThrottle]
    parser_classes = (MultiPartParser,)
    serializer_class = UploadFileSerializer
    queryset = UploadFile.objects.all()
    pagination_class = None

    def post(self, request):
        """
        本地上传文件接口
        """
        # 获取多个file
        files = request.FILES.getlist('file', [])
        result = []
        file_upload_max_size = get_upload_max_size(request.user)
        for file_obj in files:
            try:
                # file_type = file_obj.name.split(".")[-1]
                # if file_type not in ['png', 'jpeg', 'jpg', 'gif']:
                #     logger.error(f"user:{request.user} upload file type error file:{file_obj.name}")
                #     raise
                if file_obj.size > file_upload_max_size:
                    return ApiResponse(code=1003,
                                       detail=_("upload file size cannot exceed {}").format(file_upload_max_size))
            except Exception as e:
                logger.error(f"user:{request.user} upload file type error Exception:{e}")
                return ApiResponse(code=1002, detail=_("Wrong upload file type"))
            obj = UploadFile.objects.create(creator=request.user, filename=file_obj.name, is_upload=True, is_tmp=True,
                                            filepath=file_obj, mime_type=file_obj.content_type, filesize=file_obj.size)
            result.append(obj)
        return ApiResponse(data=self.get_serializer(result, many=True).data)

    def get(self, request):
        return ApiResponse(data={'file_upload_size': get_upload_max_size(request.user)})
