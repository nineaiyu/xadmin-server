#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""system 应用 WebSocket 路由（server/asgi.py 聚合）。"""

from django.urls import re_path

from . import ws, ws_monitor

app_name = "system"

urlpatterns = [
    re_path(r"ws/tasks/log/(?P<pk>[0-9a-f]{32}|[0-9a-f\-]{36})$", ws.TaskLogNotify.as_asgi()),
    re_path(r"ws/system/monitor/$", ws_monitor.MonitorNotify.as_asgi()),
]
