#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : urls
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from mfa.views import UserConfirmViewSet, UserOTPViewSet

app_name = "mfa"

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

# 敏感操作二次验证
no_detail_router.register('confirm', UserConfirmViewSet, basename='confirm')
# 个人 OTP(TOTP) 绑定管理
no_detail_router.register('otp', UserOTPViewSet, basename='otp')

urlpatterns = no_detail_router.urls + router.urls
