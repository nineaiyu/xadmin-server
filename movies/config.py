#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 11/19/2023
from django.urls import path, include

# 视频阿里云盘存储的目录名称
MOVIES_STORAGE_PREFIX = '__DO_NOT_DELETE_MOVIES__'

# 路由配置，当添加APP完成时候，会自动注入路由到总服务
URLPATTERNS = [
    path('api/movies/', include('movies.urls')),
]

# 请求白名单，支持正则表达式，可参考settings.py里面的 PERMISSION_WHITE_URL
PERMISSION_WHITE_REURL = []


