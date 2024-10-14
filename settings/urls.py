#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from settings.views.basic import BasicSettingViewSet
from settings.views.block_ip import BlockIpViewSet
from settings.views.email import EmailServerSettingViewSet
from settings.views.security import SecurityPasswordRuleViewSet, SecurityLoginLimitViewSet, \
    SecurityLoginAuthViewSet, SecurityRegisterAuthViewSet, SecurityResetPasswordAuthViewSet, \
    SecurityBindEmailAuthViewSet, \
    SecurityBindPhoneAuthViewSet
from settings.views.sms import SMSBackendAPIView, SmsSettingViewSet, SmsConfigViewSet
from settings.views.verify import VerifyCodeSettingViewSet, CaptchaSettingViewSet

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

# 设置相关
no_detail_router.register('email', EmailServerSettingViewSet, basename='email-server')

no_detail_router.register('basic', BasicSettingViewSet, basename='basic')
no_detail_router.register('password', SecurityPasswordRuleViewSet, basename='security-password')
no_detail_router.register('verify', VerifyCodeSettingViewSet, basename='verify-code')
no_detail_router.register('captcha', CaptchaSettingViewSet, basename='captcha-code')

no_detail_router.register('login/limit', SecurityLoginLimitViewSet, basename='security-login-limit')
no_detail_router.register('login/auth', SecurityLoginAuthViewSet, basename='security-login-auth')

no_detail_router.register('register/auth', SecurityRegisterAuthViewSet, basename='security-register-auth')
no_detail_router.register('reset/auth', SecurityResetPasswordAuthViewSet, basename='security-reset-auth')
no_detail_router.register('bind/email', SecurityBindEmailAuthViewSet, basename='security-bind-email-auth')
no_detail_router.register('bind/phone', SecurityBindPhoneAuthViewSet, basename='security-bind-phone-auth')

no_detail_router.register('sms', SmsSettingViewSet, basename='sms-settings')

router.register('ip/block', BlockIpViewSet, basename='ip-block')
no_detail_router.register('sms/config', SmsConfigViewSet, basename='sms-config')

urls = [
    re_path('^sms/backends$', SMSBackendAPIView.as_view(), name='sms-backends'),
]

urlpatterns = no_detail_router.urls + router.urls + urls
