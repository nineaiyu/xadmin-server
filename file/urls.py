#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""file 域路由：独立前缀挂载（server/urls.py ``^api/file/``）。

URL 前缀与 app 对齐：``/api/file/file``（列表/创建）、``/api/file/file/<pk>``、
``/api/file/file/upload`` 等——注册串与域前缀同名属轻微冗余，与
``/api/approval/approvals`` 同性质（URL 前缀即所属 app，资源段名保留语义原名
避免二次改名）。

不用空前缀注册：DRF 对空前缀路由会削掉 pattern 前导斜杠（详见
``rest_framework/routers.py::SimpleRouter.get_urls`` 的 ``if not prefix`` 分支），
detail 路由会退化成 ``/api/file<pk>`` 形态，与段边界语义冲突。

Menu.path 权限点、前端 API 层、模块裁剪 ModuleSpec 的 routes 正则已同步平移。
"""

from rest_framework.routers import SimpleRouter

from file.views.admin.file import UploadFileViewSet

app_name = "file"

router = SimpleRouter(False)
# 文件管理
router.register("file", UploadFileViewSet, basename="file")

urlpatterns = router.urls
