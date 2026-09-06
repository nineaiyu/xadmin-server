#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : email
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.sdk.sms.exceptions import CodeError, CodeExpired, CodeSendOverRate
from common.utils import random_string
from common.utils.verify_code import SendAndVerifyCodeUtil
from mfa.backends.base import BaseMFA
from mfa.const import ConfirmType


class EmailBackend(BaseMFA):
    """邮件验证码验证（挑战型：服务端先下发验证码）"""
    name = 'email'
    display_name = _('Email verification code')
    placeholder = _('Please enter the Email verification code')
    challenge_required = True
    confirm_level = ConfirmType.MFA

    @classmethod
    def global_enabled(cls) -> bool:
        return 'email' in settings.SECURITY_MFA_CONFIRM_BACKENDS and settings.EMAIL_ENABLED

    def is_active(self) -> bool:
        return bool(self.user.email)

    def send_challenge(self) -> tuple:
        subject = _('Verify code')
        code = random_string(settings.VERIFY_CODE_LENGTH, lower=settings.VERIFY_CODE_LOWER_CASE,
                             upper=settings.VERIFY_CODE_UPPER_CASE, digit=settings.VERIFY_CODE_DIGIT_CASE)
        context = {'username': self.user.username, 'title': subject, 'code': code, 'ttl': settings.VERIFY_CODE_TTL}
        message = render_to_string('msg_verify_code.html', context)
        util = SendAndVerifyCodeUtil(self.user.email, code=code, backend='email', subject=subject, message=message)
        try:
            util.gen_and_send_async()
        except CodeSendOverRate as e:
            return False, str(e.detail)
        return True, ''

    def check_code(self, code) -> tuple:
        try:
            SendAndVerifyCodeUtil(self.user.email, backend='email').verify(code)
        except CodeExpired:
            return False, str(CodeExpired.default_detail)
        except CodeError:
            return False, str(CodeError.default_detail)
        return True, ''
