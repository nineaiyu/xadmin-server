#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : password
# author : ly_13
# date : 8/6/2024

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.base.utils import AESCipherV2
from common.core.response import ApiResponse
from common.core.throttle import ResetPasswordThrottle
from common.utils.request import get_request_ip
from common.utils.token import random_string
from common.utils.verify_code import SendAndVerifyCodeUtil
from system.models import UserInfo
from system.utils.security import check_password_rules, ResetBlockUtil, LoginIpBlockUtil, get_password_check_rules
from system.utils.view import check_token_and_captcha, check_is_block


class ResetPasswordView(GenericViewSet):
    permission_classes = []
    authentication_classes = []
    throttle_classes = [ResetPasswordThrottle]

    @staticmethod
    def prepare_code_data(user):
        subject = _('Forgot password')
        code = random_string(settings.VERIFY_CODE_LENGTH, lower=settings.VERIFY_CODE_LOWER_CASE,
                             upper=settings.VERIFY_CODE_UPPER_CASE, digit=settings.VERIFY_CODE_DIGIT_CASE)
        context = {
            'user': user, 'title': subject, 'code': code, 'ttl': settings.VERIFY_CODE_TTL
        }
        message = render_to_string('msg_reset_password_code.html', context)
        content = {'subject': subject, 'message': message}
        return content, code

    @action(methods=['POST'], detail=False)
    def send(self, request, *args, **kwargs):
        if not settings.SECURITY_RESET_PASSWORD_ACCESS_ENABLED:
            return ApiResponse(code=1001, detail=_("Reset password forbidden"))

        form_type = request.data.get('form_type')
        target = request.data.get(form_type)
        if not target or form_type not in ['email', 'phone']:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        client_id, token = check_token_and_captcha(request, settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED,
                                                   settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED)

        if settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED:
            target = AESCipherV2(token).decrypt(target)

        if form_type == 'phone':
            form_type = 'sms'
            query_key = 'mobile'
            detail = _("Phone does not exist")
        else:
            query_key = 'email'
            detail = _("Email does not exist")

        user = UserInfo.objects.filter(is_active=True, **{query_key: target}).first()
        if not user:
            return ApiResponse(code=1001, detail=detail)

        reset_passwd_token = user.generate_reset_token(settings.VERIFY_CODE_TTL)
        try:
            content, code = self.prepare_code_data(user)
            SendAndVerifyCodeUtil(target, code, backend=form_type, **content).gen_and_send_async()
        except ValueError as e:
            return ApiResponse(code=1002, detail=str(e))
        return ApiResponse(data={'reset_passwd_token': reset_passwd_token},
                           detail=_("The verification code has been sent"))

    @action(methods=['POST'], detail=False)
    def reset(self, request, *args, **kwargs):
        token = request.data.get('token')
        code = request.data.get('code')
        password = request.data.get('password')
        form_type = request.data.get('form_type')
        target = request.data.get(form_type)

        if not password or not token or not code or not target or not form_type:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))

        if settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED:
            password = AESCipherV2(token).decrypt(password)
            target = AESCipherV2(token).decrypt(target)

        if form_type == 'phone':
            form_type = 'sms'

        ipaddr = get_request_ip(request)

        check_is_block(target, ipaddr, login_block=ResetBlockUtil)
        ip_block = LoginIpBlockUtil(ipaddr)
        block_util = ResetBlockUtil(target, ipaddr)

        try:
            SendAndVerifyCodeUtil(target, backend=form_type).verify(code)
        except Exception as e:
            block_util.incr_failed_count()
            ip_block.set_block_if_need()

            times_remainder = block_util.get_remainder_times()
            if times_remainder > 0:
                detail = _(
                    "{error} please enter it again. "
                    "You can also try {times_try} times "
                    "(The account will be temporarily locked for {block_time} minutes)"
                ).format(times_try=times_remainder, block_time=settings.SECURITY_LOGIN_LIMIT_TIME, error=str(e))
            else:
                detail = _("The account has been locked (please contact admin to unlock it or try"
                           " again after {} minutes)").format(settings.SECURITY_LOGIN_LIMIT_TIME)

            return ApiResponse(code=1002, detail=detail)

        instance = UserInfo.validate_reset_password_token(token)
        if not instance:
            return ApiResponse(code=1001, detail=_('Token is invalid or expired'))
        if not check_password_rules(password, instance.is_superuser):
            return ApiResponse(code=1002, detail=_('Password does not match security rules'))
        instance.set_password(password)
        instance.modifier = instance
        instance.save(update_fields=['password', 'modifier'])
        UserInfo.expired_reset_password_token(token)
        return ApiResponse(detail=_("Reset password success, return to login page"))

    def list(self, request, *args, **kwargs):
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
        return ApiResponse(data=config)
