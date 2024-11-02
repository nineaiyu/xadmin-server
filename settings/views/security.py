#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/1/2024

from common.utils import get_logger
from settings.serializers.security import SecurityPasswordRuleSerializer, SecurityLoginLimitSerializer, \
    SecurityLoginAuthSerializer, SecurityRegisterAuthSerializer, SecurityResetPasswordAuthSerializer, \
    SecurityBindEmailAuthSerializer, SecurityBindPhoneAuthSerializer, SecurityVerifyCodeSerializer, \
    SecurityCaptchaCodeSerializer
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)


class SecurityPasswordRuleViewSet(BaseSettingViewSet):
    """密码规则"""
    serializer_class = SecurityPasswordRuleSerializer
    category = "security_password"


class SecurityLoginLimitViewSet(BaseSettingViewSet):
    """登录限制"""
    serializer_class = SecurityLoginLimitSerializer
    category = "security_login_limit"


class SecurityLoginAuthViewSet(BaseSettingViewSet):
    """登录安全"""
    serializer_class = SecurityLoginAuthSerializer
    category = "security_login_auth"


class SecurityRegisterAuthViewSet(BaseSettingViewSet):
    """注册安全"""
    serializer_class = SecurityRegisterAuthSerializer
    category = "security_register_auth"


class SecurityResetPasswordAuthViewSet(BaseSettingViewSet):
    """重置密码"""
    serializer_class = SecurityResetPasswordAuthSerializer
    category = "security_reset_password_auth"


class SecurityBindEmailAuthViewSet(BaseSettingViewSet):
    """绑定邮件"""
    serializer_class = SecurityBindEmailAuthSerializer
    category = "security_bind_email_auth"


class SecurityBindPhoneAuthViewSet(BaseSettingViewSet):
    """绑定手机"""
    serializer_class = SecurityBindPhoneAuthSerializer
    category = "security_bind_phone_auth"


class SecurityVerifyCodeViewSet(BaseSettingViewSet):
    """验证码规则"""
    serializer_class = SecurityVerifyCodeSerializer
    category = "verify"


class SecurityCaptchaCodeViewSet(BaseSettingViewSet):
    """图片验证码"""
    serializer_class = SecurityCaptchaCodeSerializer
    category = "captcha"
