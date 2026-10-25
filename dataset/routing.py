#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""dataset 应用 WebSocket 路由（server/asgi.py 按 INSTALLED_APPS 自动收集）。"""

from django.urls import re_path

from dataset import ws_dashboard, ws_screen

urlpatterns = [
    # 大屏展示端通道：接收管理端远程控制指令（切换/翻页/刷新/恢复轮播）
    re_path(r"ws/screen/(?P<pk>[0-9a-f\-]{36})$", ws_screen.ScreenDisplayNotify.as_asgi()),
    # 仪表盘（工作台）观察端通道：服务端聚合数据推送（按观察者各自计算自推）
    re_path(r"ws/dashboard/(?P<pk>[0-9a-f\-]{36})$", ws_dashboard.DashboardDataNotify.as_asgi()),
]
