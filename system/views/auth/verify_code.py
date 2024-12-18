#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : code
# author : ly_13
# date : 8/10/2024

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_view, extend_schema, OpenApiParameter, OpenApiRequest
from rest_framework.generics import GenericAPIView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.fields.utils import get_file_absolute_uri
from common.swagger.utils import get_default_response_schema
from common.utils import random_string
from common.utils.request import get_request_ip
from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from settings.utils.password import get_password_check_rules
from settings.utils.security import SendVerifyCodeBlockUtil, LoginIpBlockUtil
from system.models import UserInfo
from system.utils.auth import check_token_and_captcha, check_is_block


@extend_schema_view(
    get=extend_schema(
        description="获取发送验证码配置信息",
        parameters=[
            OpenApiParameter(
                name='category',
                required=True,
                type=OpenApiTypes.STR,
                enum=['login', 'register', 'reset', 'bind_phone', 'bind_email']
            )
        ],
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'access': build_basic_type(OpenApiTypes.BOOL),
                        'captcha': build_basic_type(OpenApiTypes.BOOL),
                        'token': build_basic_type(OpenApiTypes.BOOL),
                        'encrypted': build_basic_type(OpenApiTypes.BOOL),
                        'email': build_basic_type(OpenApiTypes.BOOL),
                        'sms': build_basic_type(OpenApiTypes.BOOL),
                        'rate': build_basic_type(OpenApiTypes.NUMBER),
                        'lifetime': build_basic_type(OpenApiTypes.NUMBER),
                        'basic': build_basic_type(OpenApiTypes.BOOL),
                        'reset': build_basic_type(OpenApiTypes.BOOL),
                        'register': build_basic_type(OpenApiTypes.BOOL),
                        'password': build_array_type(build_object_type(
                            properties={
                                'key': build_basic_type(OpenApiTypes.STR),
                                'value': build_basic_type(OpenApiTypes.NUMBER),
                            }
                        ))
                    }
                )
            }
        )
    ),
    post=extend_schema(
        description="发送验证码",
        parameters=[
            OpenApiParameter(
                name='category',
                required=True,
                type=OpenApiTypes.STR,
                enum=['login', 'register', 'reset', 'bind_phone', 'bind_email']
            )
        ],
        request=OpenApiRequest(build_object_type(
            properties={
                'form_type': build_basic_type(OpenApiTypes.STR),
                'target': build_basic_type(OpenApiTypes.STR),
                'token': build_basic_type(OpenApiTypes.STR),
                'captcha_key': build_basic_type(OpenApiTypes.STR),
                'captcha_code': build_basic_type(OpenApiTypes.STR),
            }
        )),
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'verify_token': build_basic_type(OpenApiTypes.STR),
                        'verify_code': build_basic_type(OpenApiTypes.STR),
                        'extra': build_basic_type(OpenApiTypes.ANY),
                    }
                )
            }
        )
    )
)
class SendVerifyCodeAPIView(GenericAPIView):
    """获取验证码配置"""
    permission_classes = []
    authentication_classes = []

    @staticmethod
    def prepare_code_data(username):
        subject = _('Verify code')
        code = random_string(settings.VERIFY_CODE_LENGTH, lower=settings.VERIFY_CODE_LOWER_CASE,
                             upper=settings.VERIFY_CODE_UPPER_CASE, digit=settings.VERIFY_CODE_DIGIT_CASE)
        context = {
            'username': username, 'title': subject, 'code': code, 'ttl': settings.VERIFY_CODE_TTL
        }
        message = render_to_string('msg_verify_code.html', context)
        content = {'subject': subject, 'message': message}
        return content, code

    def get(self, request):
        category = request.query_params.get('category')
        get_config_func = getattr(self, 'get_%s_config' % category)
        return ApiResponse(data=get_config_func(request))

    def post(self, request):
        """发送验证码"""
        category = request.query_params.get('category')
        config = getattr(self, 'get_%s_config' % category)(request)
        if not config.get("access"):
            return ApiResponse(code=1001, detail=_("Forbidden send verification code"))

        form_type = request.data.get('form_type')
        target = request.data.get('target')

        form_types = []
        if config.get("sms"):
            form_types.append("phone")
        if config.get("basic"):
            form_types.append("username")
        if config.get('email'):
            form_types.append("email")

        if not target or form_type not in form_types:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        client_id, token = check_token_and_captcha(request, config.get("token"), config.get("captcha"))

        if config.get("encrypted"):
            target = AESCipherV2(token).decrypt(target)

        query_key = form_type
        if form_type == 'phone':
            form_type = 'sms'

        ipaddr = get_request_ip(request)

        check_is_block(target, ipaddr, login_block=SendVerifyCodeBlockUtil)

        SendVerifyCodeBlockUtil(target, ipaddr).incr_failed_count()

        try:
            username, extra = getattr(self, 'check_%s_config' % category)(request, form_type, query_key, target)
        except Exception as e:
            LoginIpBlockUtil(ipaddr).set_block_if_need()
            return ApiResponse(code=1001, detail=str(e))

        dryrun = form_type == 'username'
        try:
            content, code = self.prepare_code_data(username)
            SendAndVerifyCodeUtil(target, code, backend=form_type, dryrun=dryrun, **content).gen_and_send_async()
        except ValueError as e:
            return ApiResponse(code=1002, detail=str(e))
        cache_data = {"target": target, "form_type": form_type, "query_key": query_key, "extra": extra}
        verify_token = TokenTempCache.generate_cache_token(settings.VERIFY_CODE_TTL, cache_data)
        data = {'verify_token': verify_token, 'extra': extra}
        if dryrun:
            data['verify_code'] = code

        return ApiResponse(data=data, detail=_("The verification code has been sent"))

    @staticmethod
    def get_register_config(request):
        config = {
            'access': settings.SECURITY_REGISTER_ACCESS_ENABLED,
            'captcha': settings.SECURITY_REGISTER_CAPTCHA_ENABLED,
            'token': settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_REGISTER_ENCRYPTED_ENABLED,
            'email': settings.SECURITY_REGISTER_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
            'sms': settings.SECURITY_REGISTER_BY_SMS_ENABLED and settings.SMS_ENABLED,
            'rate': settings.VERIFY_CODE_LIMIT,
            'basic': settings.SECURITY_REGISTER_BY_BASIC_ENABLED,
            'password': get_password_check_rules(request.user)
        }
        return config

    @staticmethod
    def get_login_config(request):
        config = {
            'access': settings.SECURITY_LOGIN_ACCESS_ENABLED,
            'captcha': settings.SECURITY_LOGIN_CAPTCHA_ENABLED,
            'token': settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_LOGIN_ENCRYPTED_ENABLED,
            'email': settings.SECURITY_LOGIN_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
            'sms': settings.SECURITY_LOGIN_BY_SMS_ENABLED and settings.SMS_ENABLED,
            'basic': settings.SECURITY_LOGIN_BY_BASIC_ENABLED,
            'rate': settings.VERIFY_CODE_LIMIT,
            'lifetime': settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME').days,
            'reset': settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
            'register': settings.SECURITY_REGISTER_ACCESS_ENABLED
        }
        return config

    @staticmethod
    def get_reset_config(request):
        config = {
            'access': settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED,
            'captcha': settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED,
            'token': settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED,
            'email': settings.SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED and settings.EMAIL_ENABLED,
            'sms': settings.SECURITY_RESET_PASSWORD_BY_SMS_ENABLED and settings.SMS_ENABLED,
            'rate': settings.VERIFY_CODE_LIMIT,
            'password': get_password_check_rules(request.user)
        }
        return config

    @staticmethod
    def check_register_config(request, form_type, query_key, target):
        extra = request.data.get('extra', {})
        if form_type == 'sms':
            detail = _("Phone already exist")
        elif form_type == 'email':
            detail = _("Email already exist")
        else:
            detail = _("Username already exist")

        user = UserInfo.objects.filter(**{query_key: target}).exists()
        if user:
            raise Exception(detail)
        return '', extra

    @staticmethod
    def check_reset_config(request, form_type, query_key, target):
        extra = request.data.get('extra', {})
        if form_type == 'sms':
            detail = _("Phone does not exist")
        elif form_type == 'email':
            detail = _("Email does not exist")
        else:
            detail = _("Username does not exist")

        user = UserInfo.objects.filter(is_active=True, **{query_key: target}).first()
        if not user:
            raise Exception(detail)
        return user.username, extra

    def check_login_config(self, request, form_type, query_key, target):
        return self.check_reset_config(request, form_type, query_key, target)

    @staticmethod
    def get_bind_email_config(request):
        config = {
            'access': settings.SECURITY_BIND_EMAIL_ACCESS_ENABLED,
            'captcha': settings.SECURITY_BIND_EMAIL_CAPTCHA_ENABLED,
            'token': settings.SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED,
            'email': settings.EMAIL_ENABLED,
            'rate': settings.VERIFY_CODE_LIMIT,
        }
        return config

    @staticmethod
    def check_bind_email_config(request, form_type, query_key, target):
        extra = request.data.get('extra', {})
        user = UserInfo.objects.filter(**{query_key: target}).first()
        if user:
            extra['avatar'] = get_file_absolute_uri(user.avatar, request)
            extra['username'] = user.username
            extra['nickname'] = user.nickname
        return '', extra

    @staticmethod
    def get_bind_phone_config(request):
        config = {
            'access': settings.SECURITY_BIND_PHONE_ACCESS_ENABLED,
            'captcha': settings.SECURITY_BIND_PHONE_CAPTCHA_ENABLED,
            'token': settings.SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED,
            'encrypted': settings.SECURITY_BIND_PHONE_ENCRYPTED_ENABLED,
            'sms': settings.SMS_ENABLED,
            'rate': settings.VERIFY_CODE_LIMIT,
        }
        return config

    def check_bind_phone_config(self, request, form_type, query_key, target):
        return self.check_bind_email_config(request, form_type, query_key, target)
