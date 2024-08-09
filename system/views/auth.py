#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : auth
# author : ly_13
# date : 6/6/2023
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from rest_framework.views import APIView

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import ResetPasswordThrottle
from common.utils.request import get_request_ip
from common.utils.token import make_token_cache, random_string
from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from system.models import UserInfo
from system.utils.captcha import CaptchaAuth
from system.utils.security import get_password_check_rules, SendVerifyCodeBlockUtil, LoginIpBlockUtil, ResetBlockUtil, \
    check_password_rules
from system.utils.view import get_request_ident, check_token_and_captcha, check_is_block, verify_sms_email_code


class ResetPasswordView(APIView):
    permission_classes = []
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    def post(self, request, *args, **kwargs):
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
        TokenTempCache.expired_reset_password_token(verify_token)
        return ApiResponse(detail=_("Reset password success, return to login page"))


class TempTokenView(APIView):
    """获取临时token"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        token = make_token_cache(get_request_ident(request), time_limit=600, force_new=True).encode('utf-8')
        return ApiResponse(token=token)


class CaptchaView(APIView):
    """获取验证码"""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        return ApiResponse(**CaptchaAuth().generate())


class PasswordRulesView(APIView):
    permission_classes = []

    def get(self, request):
        return ApiResponse(data={"password_rules": get_password_check_rules(request.user)})


class SendVerifyCodeView(APIView):
    """获取验证码配置"""
    permission_classes = []
    authentication_classes = []

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
        if form_type == 'sms':
            detail = _("Phone already exist")
        else:
            detail = _("Email already exist")

        user = UserInfo.objects.filter(**{query_key: target}).exists()
        if user:
            raise Exception(detail)
        return ''

    @staticmethod
    def check_reset_config(request, form_type, query_key, target):
        if form_type == 'sms':
            detail = _("Phone does not exist")
        else:
            detail = _("Email does not exist")

        user = UserInfo.objects.filter(is_active=True, **{query_key: target}).first()
        if not user:
            raise Exception(detail)
        return user.username

    def check_login_config(self, request, form_type, query_key, target):
        return self.check_reset_config(request, form_type, query_key, target)

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
        category = request.query_params.get('category')
        config = getattr(self, 'get_%s_config' % category)(request)
        if not config.get("access"):
            return ApiResponse(code=1001, detail=_("Forbidden send verification code"))

        form_type = request.data.get('form_type')
        target = request.data.get('target')
        if not target or form_type not in ['email', 'phone']:
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
            username = getattr(self, 'check_%s_config' % category)(request, form_type, query_key, target)
        except Exception as e:
            LoginIpBlockUtil(ipaddr).set_block_if_need()
            return ApiResponse(code=1001, detail=str(e))

        try:
            content, code = self.prepare_code_data(username)
            SendAndVerifyCodeUtil(target, code, backend=form_type, **content).gen_and_send_async()
        except ValueError as e:
            return ApiResponse(code=1002, detail=str(e))
        verify_token = TokenTempCache.generate_reset_token(settings.VERIFY_CODE_TTL,
                                                           {"target": target, "form_type": form_type,
                                                            "query_key": query_key})
        return ApiResponse(data={'verify_token': verify_token}, detail=_("The verification code has been sent"))
