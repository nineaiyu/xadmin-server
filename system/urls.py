#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path, include
from rest_framework.routers import SimpleRouter

from system.views.admin.config import SystemConfigView, UserPersonalConfigView
from system.views.admin.dept import DeptView
from system.views.admin.loginlog import UserLoginLogView
from system.views.admin.menu import MenuView
from system.views.admin.modelfield import ModelLabelFieldView
from system.views.admin.notice import NoticeUserReadMessageView, NoticeMessageView
from system.views.admin.operationlog import OperationLogView
from system.views.admin.permission import DataPermissionView
from system.views.admin.role import RoleView
from system.views.admin.search import SearchDataView
from system.views.admin.user import UserView
from system.views.auth import TempTokenView, RegisterView, LoginView, LogoutView, RefreshTokenView, CaptchaView
from system.views.configs import ConfigsView
from system.views.dashboard import DashboardView
from system.views.routes import UserRoutesView
from system.views.upload import UploadView
from system.views.user.notice import UserNoticeMessage
from system.views.user.userinfo import UserInfoView

router = SimpleRouter(False)

no_auth_url = [
    re_path('^register$', RegisterView.as_view(), name='register'),
    re_path('^login$', LoginView.as_view(), name='login'),
    re_path('^auth/token$', TempTokenView.as_view(), name='temp_token'),
    re_path('^auth/captcha$', CaptchaView.as_view(), name='captcha'),
    re_path('^captcha/', include('captcha.urls')),
]

auth_url = [
    re_path('^logout$', LogoutView.as_view(), name='logout'),
    re_path('^refresh$', RefreshTokenView.as_view(), name='refresh'),
    re_path('^upload$', UploadView.as_view(), name='upload'),
]

router_url = [
    re_path('^routes$', UserRoutesView.as_view(), name='user_routes'),
]

# 个人用户信息
router.register('userinfo', UserInfoView, basename='userinfo')
router.register('user/notice', UserNoticeMessage, basename='user_notice')

# 系统设置相关路由
router.register('user', UserView, basename='user')
router.register('dept', DeptView, basename='dept')
router.register('menu', MenuView, basename='menu')
router.register('role', RoleView, basename='role')
router.register('search', SearchDataView, basename='search')
router.register('configs', ConfigsView, basename='configs')
router.register('dashboard', DashboardView, basename='dashboard')
router.register('permission', DataPermissionView, basename='permission')
router.register('field', ModelLabelFieldView, basename='model_label_field')
router.register('config/system', SystemConfigView, basename='sysconfig')
router.register('config/user', UserPersonalConfigView, basename='userconfig')
router.register('logs/operation', OperationLogView, basename='operation_log')
router.register('logs/login', UserLoginLogView, basename='login_log')

# 消息通知路由
router.register('message/notice', NoticeMessageView, basename='message_notice')
router.register('message/read', NoticeUserReadMessageView, basename='message_read')

urlpatterns = no_auth_url + auth_url + router_url + router.get_urls()
