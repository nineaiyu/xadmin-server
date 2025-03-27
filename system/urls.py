#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path, include
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from system.views.admin.config import SystemConfigViewSet, UserPersonalConfigViewSet
from system.views.admin.dept import DeptViewSet
from system.views.admin.file import UploadFileViewSet
from system.views.admin.loginlog import LoginLogViewSet
from system.views.admin.menu import MenuViewSet
from system.views.admin.modelfield import ModelLabelFieldViewSet
from system.views.admin.online import UserOnlineViewSet
from system.views.admin.operationlog import OperationLogViewSet
from system.views.admin.permission import DataPermissionViewSet
from system.views.admin.role import RoleViewSet
from system.views.admin.user import UserViewSet
from system.views.auth.login import BasicLoginAPIView, VerifyCodeLoginAPIView
from system.views.auth.logout import LogoutAPIView
from system.views.auth.register import RegisterViewAPIView
from system.views.auth.reset import ResetPasswordAPIView
from system.views.auth.rule import PasswordRulesAPIView
from system.views.auth.token import RefreshTokenAPIView, CaptchaAPIView, TempTokenAPIView
from system.views.auth.verify_code import SendVerifyCodeAPIView
from system.views.configs import ConfigsViewSet
from system.views.dashboard import DashboardViewSet
from system.views.routes import UserRoutesAPIView
from system.views.search.dept import SearchDeptViewSet
from system.views.search.menu import SearchMenuViewSet
from system.views.search.role import SearchRoleViewSet
from system.views.search.user import SearchUserViewSet
from system.views.user.login_log import UserLoginLogViewSet
from system.views.user.userinfo import UserInfoViewSet

app_name = "system"

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

no_auth_url = [
    re_path('^captcha/', include('captcha.urls')),
    re_path('^login/basic$', BasicLoginAPIView.as_view(), name='login-by-basic'),
    re_path('^login/code$', VerifyCodeLoginAPIView.as_view(), name='login-by-code'),
    re_path('^register$', RegisterViewAPIView.as_view(), name='register'),
    re_path('^auth/captcha$', CaptchaAPIView.as_view(), name='captcha'),
    re_path('^auth/token$', TempTokenAPIView.as_view(), name='temp_token'),
    re_path('^auth/verify$', SendVerifyCodeAPIView.as_view(), name='send-verify-code'),
    re_path('^auth/reset$', ResetPasswordAPIView.as_view(), name='reset-password'),

]

auth_url = [
    re_path('^logout$', LogoutAPIView.as_view(), name='logout'),
    re_path('^refresh$', RefreshTokenAPIView.as_view(), name='refresh'),
    re_path('^rules/password$', PasswordRulesAPIView.as_view(), name='password-rules'),
]

router_url = [
    re_path('^routes$', UserRoutesAPIView.as_view(), name='user_routes'),
]
# 面板信息
router.register('dashboard', DashboardViewSet, basename='dashboard')

# 仅数据搜索
router.register('search/user', SearchUserViewSet, basename='SearchUser')
router.register('search/role', SearchRoleViewSet, basename='SearchRole')
router.register('search/dept', SearchDeptViewSet, basename='SearchDept')
router.register('search/menu', SearchMenuViewSet, basename='SearchMenu')

# 个人用户信息
no_detail_router.register('userinfo', UserInfoViewSet, basename='userinfo')
router.register('user/log', UserLoginLogViewSet, basename='user_login_log')
router.register('configs', ConfigsViewSet, basename='configs')

# 系统设置相关路由
router.register('user', UserViewSet, basename='user')
router.register('dept', DeptViewSet, basename='dept')
router.register('menu', MenuViewSet, basename='menu')
router.register('role', RoleViewSet, basename='role')
router.register('permission', DataPermissionViewSet, basename='permission')
router.register('field', ModelLabelFieldViewSet, basename='model_label_field')
router.register('online', UserOnlineViewSet, basename='online_socket')

# 配置相关
router.register('config/system', SystemConfigViewSet, basename='sysconfig')
router.register('config/user', UserPersonalConfigViewSet, basename='userconfig')

# 日志相关
router.register('logs/operation', OperationLogViewSet, basename='operation_log')
router.register('logs/login', LoginLogViewSet, basename='login_log')

# 文件管理
router.register('file', UploadFileViewSet, basename='file')

urlpatterns = no_auth_url + auth_url + router_url + router.urls + no_detail_router.urls
