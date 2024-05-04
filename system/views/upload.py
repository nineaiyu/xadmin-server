#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : upload
# author : ly_13
# date : 6/26/2023
import logging

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import MultiPartParser
from rest_framework.views import APIView

from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from system.models import UploadFile
from system.utils.serializer import UploadFileSerializer

logger = logging.getLogger(__file__)


class UploadView(APIView):
    """本地上传文件接口"""
    throttle_classes = [UploadThrottle]
    parser_classes = (MultiPartParser,)

    @swagger_auto_schema(manual_parameters=[
        openapi.Parameter(
            'file', in_=openapi.IN_FORM, type=openapi.TYPE_FILE,
            description='待上传的文件', required=True
        ),
    ], operation_description='文件上传')
    def post(self, request):
        """
        本地上传文件接口
        """
        # 获取多个file
        files = request.FILES.getlist('file', [])
        result = []
        for file_obj in files:
            try:
                # file_type = file_obj.name.split(".")[-1]
                # if file_type not in ['png', 'jpeg', 'jpg', 'gif']:
                #     logger.error(f"user:{request.user} upload file type error file:{file_obj.name}")
                #     raise
                if file_obj.size > SysConfig.FILE_UPLOAD_SIZE:
                    return ApiResponse(code=1003, detail=f"文件大小不能超过 {SysConfig.FILE_UPLOAD_SIZE}")
            except Exception as e:
                logger.error(f"user:{request.user} upload file type error Exception:{e}")
                return ApiResponse(code=1002, detail="错误的文件类型")
            obj = UploadFile.objects.create(creator=request.user, filename=file_obj.name, filesize=file_obj.size,
                                            filepath=file_obj)
            result.append(obj)
        return ApiResponse(data=UploadFileSerializer(result, many=True).data)
