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
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__file__)


class SecurityPasswordRuleViewSet(BaseSettingViewSet):
    serializer_class = SecurityPasswordRuleSerializer
    category = "security_password"


class SecurityLoginLimitViewSet(BaseSettingViewSet):
    serializer_class = SecurityLoginLimitSerializer
    category = "security_login_limit"


class SecurityLoginAuthViewSet(BaseSettingViewSet):
    serializer_class = SecurityLoginAuthSerializer
    category = "security_login_auth"


class SecurityRegisterAuthViewSet(BaseSettingViewSet):
    serializer_class = SecurityRegisterAuthSerializer
    category = "security_register_auth"


class SecurityResetPasswordAuthViewSet(BaseSettingViewSet):
    serializer_class = SecurityResetPasswordAuthSerializer
    category = "security_reset_password_auth"


class SecurityBindEmailAuthViewSet(BaseSettingViewSet):
    serializer_class = SecurityBindEmailAuthSerializer
    category = "security_bind_email_auth"


class SecurityBindPhoneAuthViewSet(BaseSettingViewSet):
    serializer_class = SecurityBindPhoneAuthSerializer
    category = "security_bind_phone_auth"
