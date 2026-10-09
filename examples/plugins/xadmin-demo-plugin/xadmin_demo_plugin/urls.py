#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件路由表：由 ``config.py::URLPATTERNS`` 经宿主 ``server/urls.py`` 自动注入。

路径前缀约定 ``api/<插件名>/``：与内核一致，路由前缀同时是模块裁剪的拦截面
（``modules.py`` 声明的 ``routes`` 未写时按 ``URLPATTERNS`` 前缀推导）。
"""

from rest_framework.routers import SimpleRouter

from xadmin_demo_plugin.views import PluginNoteViewSet

app_name = "xadmin_demo_plugin"

router = SimpleRouter(False)  # False：去掉 URL 末尾斜线（与内核既有路由同口径）
router.register("note", PluginNoteViewSet, basename="plugin-note")

urlpatterns = [*router.urls]
