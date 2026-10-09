#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件 WebSocket 路由：由 ``collect_app_ws_urls`` 按 INSTALLED_APPS 自动收集。

与 ``config.py::URLPATTERNS``（HTTP 侧）同思路：app 自带 ``routing.py`` 即自动接入
``server/asgi.py``，无需改宿主工程文件。通道路径需同时在 ``modules.py`` 声明
``ws_routes``，否则模块停用时该通道仍可连接（裁剪矩阵会漏一层，守护测试会失败）。
"""

from django.urls import re_path

from xadmin_demo_plugin.consumers import PluginDemoConsumer

app_name = "xadmin_demo_plugin"

urlpatterns = [
    re_path(r"ws/plugin-demo/$", PluginDemoConsumer.as_asgi()),
]
