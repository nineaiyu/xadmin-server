#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path, include
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from system.views.admin.config import SystemConfigView, UserPersonalConfigView
from system.views.admin.dept import DeptView
from system.views.admin.file import UploadFileView
from system.views.admin.loginlog import LoginLogView
from system.views.admin.menu import MenuView
from system.views.admin.modelfield import ModelLabelFieldView
from system.views.admin.operationlog import OperationLogView
from system.views.admin.permission import DataPermissionView
from system.views.admin.role import RoleView
from system.views.admin.user import UserView
from system.views.auth.login import BasicLoginView, VerifyCodeLoginView
from system.views.auth.logout import LogoutView
from system.views.auth.register import RegisterView
from system.views.auth.reset import ResetPasswordView
from system.views.auth.rule import PasswordRulesView
from system.views.auth.token import RefreshTokenView, CaptchaView, TempTokenView
from system.views.auth.verify_code import SendVerifyCodeView
from system.views.configs import ConfigsView
from system.views.dashboard import DashboardView
from system.views.routes import UserRoutesView
from system.views.search.dept import SearchDeptView
from system.views.search.menu import SearchMenuView
from system.views.search.role import SearchRoleView
from system.views.search.user import SearchUserView
from system.views.upload import UploadView
from system.views.user.login_log import UserLoginLogView
from system.views.user.userinfo import UserInfoView

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

no_auth_url = [
    re_path('^captcha/', include('captcha.urls')),
    re_path('^login/basic$', BasicLoginView.as_view(), name='login-by-basic'),
    re_path('^login/code$', VerifyCodeLoginView.as_view(), name='login-by-code'),
    re_path('^register$', RegisterView.as_view(), name='register'),
    re_path('^auth/captcha$', CaptchaView.as_view(), name='captcha'),
    re_path('^auth/token$', TempTokenView.as_view(), name='temp_token'),
    re_path('^auth/verify$', SendVerifyCodeView.as_view(), name='send-verify-code'),
    re_path('^auth/reset$', ResetPasswordView.as_view(), name='reset-password'),

]

auth_url = [
    re_path('^logout$', LogoutView.as_view(), name='logout'),
    re_path('^refresh$', RefreshTokenView.as_view(), name='refresh'),
    re_path('^upload$', UploadView.as_view(), name='upload'),
    re_path('^rules/password$', PasswordRulesView.as_view(), name='password-rules'),
]

router_url = [
    re_path('^routes$', UserRoutesView.as_view(), name='user_routes'),
]
# 面板信息
router.register('dashboard', DashboardView, basename='dashboard')

# 仅数据搜索
router.register('search/user', SearchUserView, basename='SearchUser')
router.register('search/role', SearchRoleView, basename='SearchRole')
router.register('search/dept', SearchDeptView, basename='SearchDept')
router.register('search/menu', SearchMenuView, basename='SearchMenu')

# 个人用户信息
no_detail_router.register('userinfo', UserInfoView, basename='userinfo')
router.register('user/log', UserLoginLogView, basename='user_login_log')
router.register('configs', ConfigsView, basename='configs')

# 系统设置相关路由
router.register('user', UserView, basename='user')
router.register('dept', DeptView, basename='dept')
router.register('menu', MenuView, basename='menu')
router.register('role', RoleView, basename='role')
router.register('permission', DataPermissionView, basename='permission')
router.register('field', ModelLabelFieldView, basename='model_label_field')

# 配置相关
router.register('config/system', SystemConfigView, basename='sysconfig')
router.register('config/user', UserPersonalConfigView, basename='userconfig')

# 日志相关
router.register('logs/operation', OperationLogView, basename='operation_log')
router.register('logs/login', LoginLogView, basename='login_log')

# 文件管理
router.register('file', UploadFileView, basename='file')

urlpatterns = no_auth_url + auth_url + router_url + router.urls + no_detail_router.urls
