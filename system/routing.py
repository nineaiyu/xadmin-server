#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""system 应用 WebSocket 路由（server/asgi.py 经 collect_app_ws_urls 自动收集）。

任务日志推送路由已随 task 域拆分迁往 task/routing.py，此处留存 platform 面。
"""

from django.urls import re_path

from . import ws_monitor

urlpatterns = [
    re_path(r"ws/system/monitor/$", ws_monitor.MonitorNotify.as_asgi()),
]
