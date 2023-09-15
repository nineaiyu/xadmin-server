#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : user
# author : ly_13
# date : 6/16/2023
import logging

from django.db.models import FileField
from rest_framework.decorators import action

from common.core.modelset import OwnerModelSet
from common.core.response import ApiResponse
from system.utils.serializer import UserInfoSerializer

logger = logging.getLogger(__name__)


class UserInfoView(OwnerModelSet):
    serializer_class = UserInfoSerializer

    def get_object(self):
        return self.request.user

    @action(methods=['post'], detail=False)
    def upload(self, request, *args, **kwargs):
        files = request.FILES.getlist('file', [])
        user_obj = self.get_object()
        file_obj = files[0]
        try:
            file_type = file_obj.name.split(".")[-1]
            if file_type not in ['png', 'jpeg', 'jpg', 'gif']:
                logger.error(f"user:{user_obj} upload file type error file:{file_obj.name}")
                raise
            if file_obj.size > 1024 * 1024 * 3:
                return ApiResponse(code=1003, detail="图片大小不能超过3兆")
        except Exception as e:
            logger.error(f"user:{user_obj} upload file type error Exception:{e}")
            return ApiResponse(code=1002, detail="错误的图片类型")
        delete_avatar_name = None
        if user_obj.avatar:
            delete_avatar_name = user_obj.avatar.name
        user_obj.avatar = file_obj
        user_obj.save(update_fields=['avatar'])
        if delete_avatar_name:
            FileField(name=delete_avatar_name).storage.delete(delete_avatar_name)
        return ApiResponse()

    @action(methods=['post'], detail=False)
    def reset_password(self, request, *args, **kwargs):
        old_password = request.data.get('old_password')
        sure_password = request.data.get('sure_password')
        if old_password and sure_password:
            instance = self.get_object()
            if not instance.check_password(old_password):
                raise Exception('旧密码校验失败')
            instance.set_password(sure_password)
            instance.save(update_fields=['password'])
            return ApiResponse()
        return ApiResponse(code=1001, detail='修改失败')
