#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : otp
import pyotp
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from mfa.backends.base import BaseMFA
from mfa.cache import UsedOtpCodeCache
from mfa.const import ConfirmType


class OtpBackend(BaseMFA):
    """OTP(TOTP) 动态口令验证"""
    name = 'otp'
    display_name = _('OTP verification code')
    placeholder = _('Please enter the 6-digit dynamic code')
    confirm_level = ConfirmType.MFA

    @classmethod
    def global_enabled(cls) -> bool:
        return 'otp' in settings.SECURITY_MFA_CONFIRM_BACKENDS

    def is_active(self) -> bool:
        return bool(self.user.otp_secret_key)

    def check_code(self, code) -> tuple:
        if not self.user.otp_secret_key:
            return False, _('OTP is not bound')
        used = UsedOtpCodeCache(self.user, code)
        if used.exists():
            return False, _('The verification code has already been used')
        if not self.verify_code(self.user.otp_secret_key, code):
            return False, _('The OTP verification code is incorrect')
        used.mark()
        return True, ''

    @staticmethod
    def verify_code(secret, code) -> bool:
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=settings.SECURITY_MFA_OTP_VALID_WINDOW)

    @staticmethod
    def generate_secret() -> str:
        return pyotp.random_base32()

    @staticmethod
    def get_provisioning_uri(user, secret) -> str:
        """生成 otpauth 绑定 URI，前端据此渲染二维码"""
        return pyotp.TOTP(secret).provisioning_uri(
            name=user.username, issuer_name=settings.SECURITY_MFA_OTP_ISSUER
        )
