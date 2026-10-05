#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 6/12/2024


from django.urls import include, path

# 路由配置，当添加APP完成时候，会自动注入路由到总服务
URLPATTERNS = [
    path("api/demo/", include("demo.urls")),
]
# 请求白名单，支持正则表达式，可参考settings.py里面的 PERMISSION_WHITE_URL
PERMISSION_WHITE_REURL: list[str] = []

# 审批终态回写业务同步器（biz_type → 导入路径）：由 approval/biz_sync.py 注册表
# 按声明收集，新增业务回写零核心文件改动（demo 卸载时本声明自然不参与收集）
APPROVAL_BIZ_SYNCERS = {
    "demo_book": "demo.services.sync_book_instance",
}
