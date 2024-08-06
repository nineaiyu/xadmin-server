#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from settings.views.basic import BasicSettingView
from settings.views.email import EmailServerSettingView, EmailTestSettingView
from settings.views.security import SecurityPasswordRuleView, SecurityLoginLimitView, \
    SecurityLoginAuthView, SecurityRegisterAuthView
from settings.views.sms import SMSBackendView, SmsSettingView, SmsConfigView

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

# 设置相关
no_detail_router.register('email/server', EmailServerSettingView, basename='email-server')
no_detail_router.register('email/test', EmailTestSettingView, basename='email-test')

no_detail_router.register('basic', BasicSettingView, basename='basic')
no_detail_router.register('password', SecurityPasswordRuleView, basename='security-password')

no_detail_router.register('login/limit', SecurityLoginLimitView, basename='security-login-limit')
no_detail_router.register('login/auth', SecurityLoginAuthView, basename='security-login-auth')

no_detail_router.register('register/auth', SecurityRegisterAuthView, basename='security-register-auth')

no_detail_router.register('sms', SmsSettingView, basename='sms-settings')

router.register('sms/backend', SMSBackendView, basename='sms-backend')
no_detail_router.register('sms/config', SmsConfigView, basename='sms-config')

urlpatterns = no_detail_router.urls + router.urls
