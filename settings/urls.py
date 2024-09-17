#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from settings.views.basic import BasicSettingView
from settings.views.block_ip import BlockIpView
from settings.views.email import EmailServerSettingView
from settings.views.security import SecurityPasswordRuleView, SecurityLoginLimitView, \
    SecurityLoginAuthView, SecurityRegisterAuthView, SecurityResetPasswordAuthView, SecurityBindEmailAuthView, \
    SecurityBindPhoneAuthView
from settings.views.sms import SMSBackendView, SmsSettingView, SmsConfigView
from settings.views.verify import VerifyCodeSettingView, CaptchaSettingView

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

# 设置相关
no_detail_router.register('email', EmailServerSettingView, basename='email-server')

no_detail_router.register('basic', BasicSettingView, basename='basic')
no_detail_router.register('password', SecurityPasswordRuleView, basename='security-password')
no_detail_router.register('verify', VerifyCodeSettingView, basename='verify-code')
no_detail_router.register('captcha', CaptchaSettingView, basename='captcha-code')

no_detail_router.register('login/limit', SecurityLoginLimitView, basename='security-login-limit')
no_detail_router.register('login/auth', SecurityLoginAuthView, basename='security-login-auth')

no_detail_router.register('register/auth', SecurityRegisterAuthView, basename='security-register-auth')
no_detail_router.register('reset/auth', SecurityResetPasswordAuthView, basename='security-reset-auth')
no_detail_router.register('bind/email', SecurityBindEmailAuthView, basename='security-bind-email-auth')
no_detail_router.register('bind/phone', SecurityBindPhoneAuthView, basename='security-bind-phone-auth')

no_detail_router.register('sms', SmsSettingView, basename='sms-settings')

router.register('ip/block', BlockIpView, basename='ip-block')
no_detail_router.register('sms/config', SmsConfigView, basename='sms-config')

urls = [
    re_path('^sms/backends$', SMSBackendView.as_view(), name='sms-backends'),
]

urlpatterns = no_detail_router.urls + router.urls + urls
