#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : sms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.sdk.sms.exceptions import CodeError, CodeExpired, CodeSendOverRate
from common.utils.verify_code import SendAndVerifyCodeUtil
from mfa.backends.base import BaseMFA
from mfa.const import ConfirmType


class SmsBackend(BaseMFA):
    """短信验证码验证（挑战型：服务端先下发验证码）"""
    name = 'sms'
    display_name = _('SMS verification code')
    placeholder = _('Please enter the SMS verification code')
    challenge_required = True
    confirm_level = ConfirmType.MFA

    @classmethod
    def global_enabled(cls) -> bool:
        return 'sms' in settings.SECURITY_MFA_CONFIRM_BACKENDS and settings.SMS_ENABLED

    def is_active(self) -> bool:
        return bool(self.user.phone)

    def send_challenge(self) -> tuple:
        try:
            SendAndVerifyCodeUtil(self.user.phone, backend='sms').gen_and_send_async()
        except CodeSendOverRate as e:
            return False, str(e.detail)
        return True, ''

    def check_code(self, code) -> tuple:
        try:
            SendAndVerifyCodeUtil(self.user.phone, backend='sms').verify(code)
        except CodeExpired:
            return False, str(CodeExpired.default_detail)
        except CodeError:
            return False, str(CodeError.default_detail)
        return True, ''
