#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : upload
# author : ly_13
# date : 6/26/2023
import logging

from rest_framework.views import APIView

from common.core.response import ApiResponse
from system.models import UserInfo

logger = logging.getLogger(__file__)


class UploadView(APIView):

    def post(self, request):
        """
        该方法 主要是本地上传文件接口
        :param request:
        :return:
        """
        # 获取多个file
        files = request.FILES.getlist('file', [])
        user_obj = request.user
        uid = request.query_params.get('uid')
        if user_obj.is_superuser and uid:
            user_obj = UserInfo.objects.filter(pk=uid).first()
        if user_obj:
            file_obj = files[0]
            try:
                file_type = file_obj.name.split(".")[-1]
                if file_type not in ['png', 'jpeg', 'jpg', 'gif']:
                    logger.error(f"user:{request.user} upload file type error file:{file_obj.name}")
                    raise
            except Exception as e:
                logger.error(f"user:{request.user} upload file type error Exception:{e}")
                return ApiResponse(code=1002, detail="错误的图片类型")
            if user_obj.avatar:
                user_obj.avatar.delete()
            user_obj.avatar = file_obj
            user_obj.save(update_fields=['avatar'])
            return ApiResponse()
        return ApiResponse(code=1004, detail="数据异常")
