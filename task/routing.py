#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 应用 WebSocket 路由（server/asgi.py 经 collect_app_ws_urls 自动收集）。"""

from django.urls import re_path

from . import ws

urlpatterns = [
    re_path(r"ws/tasks/log/(?P<pk>[0-9a-f]{32}|[0-9a-f\-]{36})$", ws.TaskLogNotify.as_asgi()),
]
