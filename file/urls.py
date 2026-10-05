#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""file 域路由：经 system/urls.py 以 ``path("", include())`` 同前缀挂载。

不设 app_name：注册项并入 system 命名空间——/api/system/* 路径、system: 视图名、
权限点与 menu.json 全部零变化（ADR-057 D1.2 口径）。
"""

from rest_framework.routers import SimpleRouter

from file.views.admin.file import UploadFileViewSet

router = SimpleRouter(False)
# 文件管理
router.register("file", UploadFileViewSet, basename="file")

urlpatterns = router.urls
