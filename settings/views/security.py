#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/1/2024

from common.utils import get_logger
from settings.serializers.security import SecurityPasswordRuleSerializer, SecurityLoginLimitSerializer, \
    SecurityLoginAuthSerializer, SecurityRegisterAuthSerializer, SecurityResetPasswordAuthSerializer, \
    SecurityBindEmailAuthSerializer, SecurityBindPhoneAuthSerializer
from settings.views.settings import BaseSettingView

logger = get_logger(__file__)


class SecurityPasswordRuleView(BaseSettingView):
    serializer_class = SecurityPasswordRuleSerializer
    category = "security_password"


class SecurityLoginLimitView(BaseSettingView):
    serializer_class = SecurityLoginLimitSerializer
    category = "security_login_limit"


class SecurityLoginAuthView(BaseSettingView):
    serializer_class = SecurityLoginAuthSerializer
    category = "security_login_auth"


class SecurityRegisterAuthView(BaseSettingView):
    serializer_class = SecurityRegisterAuthSerializer
    category = "security_register_auth"


class SecurityResetPasswordAuthView(BaseSettingView):
    serializer_class = SecurityResetPasswordAuthSerializer
    category = "security_reset_password_auth"


class SecurityBindEmailAuthView(BaseSettingView):
    serializer_class = SecurityBindEmailAuthSerializer
    category = "security_bind_email_auth"


class SecurityBindPhoneAuthView(BaseSettingView):
    serializer_class = SecurityBindPhoneAuthSerializer
    category = "security_bind_phone_auth"
