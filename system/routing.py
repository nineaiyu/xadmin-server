#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""system 应用 WebSocket 路由（server/asgi.py 聚合）。"""

from django.urls import re_path

from . import ws, ws_monitor, ws_screen

app_name = "system"

urlpatterns = [
    re_path(r"ws/tasks/log/(?P<pk>[0-9a-f]{32}|[0-9a-f\-]{36})$", ws.TaskLogNotify.as_asgi()),
    re_path(r"ws/system/monitor/$", ws_monitor.MonitorNotify.as_asgi()),
    # 大屏展示端通道：接收管理端远程控制指令（切换/翻页/刷新/恢复轮播）
    re_path(r"ws/screen/(?P<pk>[0-9a-f\-]{36})$", ws_screen.ScreenDisplayNotify.as_asgi()),
]
