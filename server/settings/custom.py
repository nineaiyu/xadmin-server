#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : custom
# author : ly_13
# date : 11/14/2024

# 访问白名单配置，无需权限配置, key为路由，value为列表，对应的是请求方式， * 表示全部请求方式, 请求方式为大写
PERMISSION_WHITE_URL = {
    "^/api/system/login$": ['*'],
    "^/api/system/logout$": ['*'],
    "^/api/system/userinfo$": ['GET'],
    "^/api/system/routes$": ['*'],
    "^/api/system/dashboard/": ['*'],
    "^/api/.*choices$": ['*'],
    "^/api/.*search-fields$": ['*'],
    "^/api/common/resources/cache$": ['*'],
    "^/api/notifications/site-messages/unread$": ['*'],
}

# 前端权限路由 忽略配置
ROUTE_IGNORE_URL = [
    "^/api/system/.*choices$",  # 每个方法都有该路由，则忽略即可
    "^/api/.*search-fields$",  # 每个方法都有该路由，则忽略即可
    "^/api/.*search-columns$",  # 该路由使用list权限字段，无需重新配置
    "^/api/settings/.*search-columns$",  # 该路由使用list权限字段，无需重新配置
    "^/api/system/dashboard/",  # 忽略dashboard路由
    "^/api/system/captcha",  # 忽略图片验证码路由
]

# 访问权限配置
PERMISSION_SHOW_PREFIX = [
    r'api/system',
    r'api/settings',
    r'api/notifications',
    r'api/flower',
    r'api-docs',
]
# 数据权限配置
PERMISSION_DATA_AUTH_APPS = [
    'system',
    'settings',
    'notifications'
]

API_LOG_ENABLE = True
API_LOG_METHODS = ["POST", "DELETE", "PUT", "PATCH"]  # 'ALL'

# 忽略日志记录, 支持model 或者 request_path, 不支持正则
API_LOG_IGNORE = {
    'system.OperationLog': ['GET'],
    '/api/common/api/health': ['GET'],
}

# 在操作日志中详细记录的请求模块映射
API_MODEL_MAP = {
    "/api/system/refresh": "Token刷新",
    "/api/flower": "定时任务",
}
