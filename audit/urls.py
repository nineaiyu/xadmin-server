#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit 域路由：经 system/urls.py 以 ``path("", include())`` 同前缀挂载。

不设 app_name：注册项并入 system 命名空间——/api/system/* 路径、system: 视图名、
权限点与 menu.json 全部零变化（ADR-057 D1.2 口径）。
"""

from rest_framework.routers import SimpleRouter

from audit.views.admin.loginlog import LoginLogViewSet
from audit.views.admin.mask import DataMaskRuleViewSet
from audit.views.admin.operationlog import OperationLogViewSet
from audit.views.user.login_log import UserLoginLogViewSet

router = SimpleRouter(False)
# 日志相关
router.register("logs/operation", OperationLogViewSet, basename="operation_log")
router.register("logs/login", LoginLogViewSet, basename="login_log")
# 脱敏规则
router.register("mask-rules", DataMaskRuleViewSet, basename="data_mask_rule")
# 个人登录日志（user 面口径）
router.register("user/log", UserLoginLogViewSet, basename="user_login_log")

urlpatterns = router.urls
