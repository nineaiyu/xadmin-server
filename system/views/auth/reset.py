#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : reset
# author : ly_13
# date : 8/10/2024
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.generics import GenericAPIView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import ResetPasswordThrottle
from common.swagger.utils import get_default_response_schema
from common.utils.verify_code import TokenTempCache
from settings.utils.password import check_password_rules
from settings.utils.security import ResetBlockUtil
from system.models import UserInfo
from system.utils.auth import verify_sms_email_code


class ResetPasswordAPIView(GenericAPIView):
    """重置密码"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'verify_token': build_basic_type(OpenApiTypes.STR),
                    'verify_code': build_basic_type(OpenApiTypes.STR),
                }
            )
        ),
        responses=get_default_response_schema()
    )
    def post(self, request, *args, **kwargs):
        """重置密码"""
        query_key, target, verify_token = verify_sms_email_code(request, ResetBlockUtil)
        password = request.data.get('password')
        if not password:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        if settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED:
            password = AESCipherV2(verify_token).decrypt(password)

        instance = UserInfo.objects.get(**{query_key: target})
        if not check_password_rules(password, instance.is_superuser):
            return ApiResponse(code=1002, detail=_('Password does not match security rules'))
        instance.set_password(password)
        instance.modifier = instance
        instance.save(update_fields=['password', 'modifier'])
        TokenTempCache.expired_cache_token(verify_token)
        return ApiResponse(detail=_("Reset password success, return to login page"))
