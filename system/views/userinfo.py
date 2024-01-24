#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : userinfo
# author : ly_13
# date : 6/16/2023
import logging

from rest_framework.decorators import action

from common.base.magic import cache_response
from common.base.utils import get_choices_dict
from common.core.modelset import OwnerModelSet, UploadFileAction
from common.core.response import ApiResponse
from system.models import UserInfo
from system.utils.serializer import UserInfoSerializer

logger = logging.getLogger(__name__)


class UserInfoView(OwnerModelSet, UploadFileAction):
    serializer_class = UserInfoSerializer
    FILE_UPLOAD_FIELD = 'avatar'

    def get_object(self):
        return self.request.user

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def retrieve(self, request, *args, **kwargs):
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(UserInfo.GenderChoices.choices))

    @action(methods=['post'], detail=False)
    def reset_password(self, request, *args, **kwargs):
        old_password = request.data.get('old_password')
        sure_password = request.data.get('sure_password')
        if old_password and sure_password:
            instance = self.get_object()
            if not instance.check_password(old_password):
                return ApiResponse(code=1001, detail='旧密码校验失败')
            instance.set_password(sure_password)
            instance.modifier = request.user
            instance.save(update_fields=['password', 'modifier'])
            return ApiResponse()
        return ApiResponse(code=1001, detail='修改失败')
