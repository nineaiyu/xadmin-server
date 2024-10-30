#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : userinfo
# author : ly_13
# date : 6/16/2023

from django.conf import settings
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser

from common.core.modelset import DetailUpdateModelSet, UploadFileAction, ChoicesAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from common.utils.verify_code import TokenTempCache
from settings.utils.security import ResetBlockUtil
from system.models import UserInfo
from system.notifications import ResetPasswordSuccessMsg
from system.serializers.userinfo import UserInfoSerializer, ChangePasswordSerializer
from system.utils.auth import verify_sms_email_code

logger = get_logger(__name__)


class UserInfoViewSet(DetailUpdateModelSet, ChoicesAction, UploadFileAction):
    """个人"""
    serializer_class = UserInfoSerializer
    FILE_UPLOAD_FIELD = 'avatar'
    choices_models = [UserInfo]
    queryset = UserInfo.objects.none()

    def get_object(self):
        return self.request.user

    def get_queryset(self):
        return UserInfo.objects.filter(pk=self.request.user.pk)

    def retrieve(self, request, *args, **kwargs):
        """获取{cls}信息"""
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(**data, config={
            'FRONT_END_WEB_WATERMARK_ENABLED': settings.FRONT_END_WEB_WATERMARK_ENABLED
        })

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['post'], detail=False, url_path='reset-password', serializer_class=ChangePasswordSerializer)
    def reset_password(self, request, *args, **kwargs):
        """修改{cls}密码"""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        ResetPasswordSuccessMsg(instance, request).publish_async()
        return ApiResponse()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(properties={'file': build_basic_type(OpenApiTypes.BINARY)})
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=False, parser_classes=(MultiPartParser,))
    def upload(self, request, *args, **kwargs):
        """上传{cls}头像"""
        return super().upload(request, *args, **kwargs)

    @extend_schema(
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
    @action(methods=['post'], detail=False, url_path='bind')
    def bind(self, request, *args, **kwargs):
        """绑定{cls}邮箱或手机"""
        query_key, target, verify_token = verify_sms_email_code(request, ResetBlockUtil)
        instance = UserInfo.objects.filter(**{query_key: target}).first()
        if instance:
            setattr(instance, query_key, '')
            instance.save(update_fields=(query_key,))
        setattr(request.user, query_key, target)
        request.user.save(update_fields=(query_key,))
        TokenTempCache.expired_cache_token(verify_token)
        return ApiResponse()
