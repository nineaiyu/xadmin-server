#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""宿主接线声明（二开扩展点）：``URLPATTERNS`` 由 ``server/urls.py`` 自动注入。

注入条件：本 app 出现在宿主 ``config.yml`` 的 ``XADMIN_APPS`` 中
（``server/settings/apps.py::build_installed_apps`` 把它装配进 INSTALLED_APPS，
``common/core/utils.py::auto_register_app_url`` 再读取下面的 URLPATTERNS）。
新增前缀还会被自动并入 ``PERMISSION_SHOW_PREFIX``（权限点可见性前缀）与
``PERMISSION_DATA_AUTH_APPS``（数据权限候选 app）——与业务 app 完全同通路。
"""

from django.urls import include, path

# 路由配置：app 注册进 XADMIN_APPS 后自动注入到总路由（无需改宿主 urls.py）
URLPATTERNS = [
    path("api/plugin-demo/", include("xadmin_demo_plugin.urls")),
]

# 请求白名单（正则，免登录可访问）：本插件无匿名端点
PERMISSION_WHITE_REURL: list[str] = []
