#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : register
# author : ly_13
# date : 8/8/2024

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import RegisterThrottle, ResetPasswordThrottle
from common.utils.verify_code import TokenTempCache
from system.models import UserInfo, DeptInfo
from system.utils.security import check_password_rules, ResetBlockUtil,  RegisterBlockUtil
from system.utils.view import get_token_lifetime, save_login_log,  verify_sms_email_code


class RegisterView(APIView):
    """用户注册"""
    permission_classes = []
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    def post(self, request, *args, **kwargs):
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

        if UserInfo.objects.filter(**{query_key: target}).exists():
            return ApiResponse(code=1002, detail=_("The account already exists, please try another one"))
        username = target
        user = UserInfo.objects.create_user(username=username, password=password, first_name=username,
                                            nickname=username, **{query_key: target})

        update_fields = ['last_login']

        if channel and user:
            dept = DeptInfo.objects.filter(is_active=True, auto_bind=True, code=channel).first()
            if not dept:
                dept = DeptInfo.objects.filter(is_active=True, auto_bind=True).first()
            if dept:
                user.dept = dept
                user.dept_belong = dept
                update_fields.extend(['dept_belong', 'dept'])

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
