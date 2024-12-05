#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : register
# author : ly_13
# date : 8/8/2024

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle
from common.swagger.utils import get_default_response_schema
from settings.utils.password import check_password_rules
from settings.utils.security import RegisterBlockUtil
from system.models import DeptInfo, UserInfo
from system.utils.auth import get_token_lifetime, save_login_log, verify_sms_email_code


class RegisterViewAPIView(GenericAPIView):
    """用户注册"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'channel': build_basic_type(OpenApiTypes.STR),
                    'password': build_basic_type(OpenApiTypes.STR),
                    'verify_token': build_basic_type(OpenApiTypes.STR),
                    'verify_code': build_basic_type(OpenApiTypes.STR),
                },
                required=['verify_token', 'verify_code'],
            )
        ),
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'refresh': build_basic_type(OpenApiTypes.STR),
                        'access': build_basic_type(OpenApiTypes.STR),
                        'access_token_lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'refresh_token_lifetime': build_basic_type(OpenApiTypes.NUMBER)
                    }
                )
            }
        )
    )
    def post(self, request, *args, **kwargs):
        """注册账户"""
        if not settings.SECURITY_REGISTER_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Registration forbidden"))

        channel = request.data.get('channel', 'default')

        query_key, target, verify_token = verify_sms_email_code(request, RegisterBlockUtil)
        password = request.data.get('password')
        if not password:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        if settings.SECURITY_REGISTER_ENCRYPTED_ENABLED:
            password = AESCipherV2(verify_token).decrypt(password)

        if not check_password_rules(password):
            return ApiResponse(code=1001, detail=_("Password does not match security rules"))

        username = target
        default = {query_key: target}
        if query_key == 'username':
            default = {}

        with cache.lock(f"_LOCKER_REGISTER_USER", timeout=10):  # 加锁是为了防止并发注册导致手机，邮箱或者用户名重复
            if UserInfo.objects.filter(**{query_key: target}).exists():
                return ApiResponse(code=1002, detail=_("The account already exists, please try another one"))
            user = UserInfo.objects.create_user(username=username, password=password, nickname=username, **default)

        update_fields = ['last_login']

        if channel and user:
            dept = DeptInfo.objects.filter(is_active=True, auto_bind=True, code=channel).first()
            if not dept:
                dept = DeptInfo.objects.filter(is_active=True, auto_bind=True).first()
            if dept:
                user.dept = dept
                user.creator = user
                user.dept_belong = dept
                update_fields.extend(['dept_belong', 'dept', 'creator'])

        refresh = RefreshToken.for_user(user)
        result = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        user.last_login = timezone.now()
        user.save(update_fields=update_fields)
        result.update(**get_token_lifetime(user))
        request.user = user
        save_login_log(request)
        return ApiResponse(data=result)
