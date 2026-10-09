#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit 域路由：独立前缀挂载（server/urls.py ``^api/audit/``）。

URL 前缀与 app 对齐：``/api/audit/...``；Menu.path 权限点、前端 API 层、
模块裁剪 ModuleSpec 的 routes 正则已同步平移。
"""

from rest_framework.routers import SimpleRouter

from audit.views.admin.loginlog import LoginLogViewSet
from audit.views.admin.mask import DataMaskRuleViewSet
from audit.views.admin.operationlog import OperationLogViewSet
from audit.views.user.login_log import UserLoginLogViewSet

app_name = "audit"

router = SimpleRouter(False)
# 日志相关
router.register("logs/operation", OperationLogViewSet, basename="operation_log")
router.register("logs/login", LoginLogViewSet, basename="login_log")
# 脱敏规则
router.register("mask-rules", DataMaskRuleViewSet, basename="data_mask_rule")
# 个人登录日志（user 面口径）
router.register("user/log", UserLoginLogViewSet, basename="user_login_log")

urlpatterns = router.urls
