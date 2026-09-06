#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : password
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from mfa.backends.base import BaseMFA
from mfa.const import ConfirmType


class PasswordBackend(BaseMFA):
    """登录密码验证（无验证码，凭已有凭证确认身份）"""
    name = 'password'
    display_name = _('Login password')
    placeholder = _('Please enter your login password')
    confirm_level = ConfirmType.PASSWORD

    @classmethod
    def global_enabled(cls) -> bool:
        return 'password' in settings.SECURITY_MFA_CONFIRM_BACKENDS

    def is_active(self) -> bool:
        return self.user.has_usable_password()

    def check_code(self, code) -> tuple:
        if code and self.user.check_password(code):
            return True, ''
        return False, _('The password is incorrect')
