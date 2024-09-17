#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : userinfo
# author : ly_13
# date : 6/16/2023
import logging

from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser

from common.base.magic import cache_response
from common.base.utils import get_choices_dict
from common.core.modelset import DetailUpdateModelSet, UploadFileAction, ChoicesAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils.verify_code import TokenTempCache
from settings.utils.security import ResetBlockUtil
from system.models import UserInfo
from system.notifications import ResetPasswordSuccessMsg
from system.serializers.userinfo import UserInfoSerializer, ChangePasswordSerializer
from system.utils.auth import verify_sms_email_code

logger = logging.getLogger(__name__)


class UserInfoView(DetailUpdateModelSet, ChoicesAction, UploadFileAction):
    """用户个人信息管理"""
    serializer_class = UserInfoSerializer
    FILE_UPLOAD_FIELD = 'avatar'
    choices_models = [UserInfo]

    def get_object(self):
        return self.request.user

    def get_queryset(self):
        return UserInfo.objects.filter(pk=self.request.user.pk)

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def retrieve(self, request, *args, **kwargs):
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(UserInfo.GenderChoices.choices))

    @extend_schema(description='用户修改密码', responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='reset-password', serializer_class=ChangePasswordSerializer)
    def reset_password(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        ResetPasswordSuccessMsg(instance, request).publish_async()
        return ApiResponse()

    @extend_schema(
        description="绑定邮箱或者手机",
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'verify_token': build_basic_type(OpenApiTypes.STR),
                    'verify_code': build_basic_type(OpenApiTypes.STR),
                },
                required=['verify_token', 'verify_code'],
            )
        ),
        responses=get_default_response_schema()
    )
    @extend_schema(
        description="上传头像",
        request=OpenApiRequest(
            build_object_type(properties={'file': build_basic_type(OpenApiTypes.BINARY)})
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=False, parser_classes=(MultiPartParser,))
    def upload(self, request, *args, **kwargs):
        return super().upload(request, *args, **kwargs)

    @action(methods=['post'], detail=False, url_path='bind')
    def bind(self, request, *args, **kwargs):
        query_key, target, verify_token = verify_sms_email_code(request, ResetBlockUtil)
        instance = UserInfo.objects.filter(**{query_key: target}).first()
        if instance:
            setattr(instance, query_key, '')
            instance.save(update_fields=(query_key,))
        setattr(request.user, query_key, target)
        request.user.save(update_fields=(query_key,))
        TokenTempCache.expired_cache_token(verify_token)
        return ApiResponse()
