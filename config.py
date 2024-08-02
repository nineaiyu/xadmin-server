#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 6/11/2024

# debug为false的时候，如果遇到静态文件无法访问，比如api文档无法正常打开，需要通过下面命令收集静态文件
# python manage.py collectstatic

DEBUG = True

ALLOWED_HOSTS = ["*"]

### 更多数据库配置，参考官方文档：https://docs.djangoproject.com/zh-hans/5.0/ref/databases/

# # mysql 数据库配置
# # create database xadmin default character set utf8 COLLATE utf8_general_ci;
# # grant all on xadmin.* to server@'127.0.0.1' identified by 'KGzKjZpWBp4R4RSa';
# DB_ENGINE = 'django.db.backends.mysql'
# DB_HOST = 'mariadb'
# DB_PORT = 3306
# DB_USER = 'server'
# DB_DATABASE = 'xadmin'
# DB_PASSWORD = 'KGzKjZpWBp4R4RSa'
# DB_OPTIONS = {'init_command': 'SET sql_mode="STRICT_TRANS_TABLES"', 'charset': 'utf8mb4'}


# sqlite3 配置，和 mysql配置 二选一, 默认sqlite数据库
DB_ENGINE = 'django.db.backends.sqlite3'

# 缓存配置
REDIS_HOST = "redis"
REDIS_PORT = 46379
REDIS_PASSWORD = "nineven"

# 需要将创建的应用写到里面
XADMIN_APPS = [
    # "demo.apps.DemoConfig",
]

# 速率限制配置
DEFAULT_THROTTLE_RATES = {}

# redis key，建议开发的时候，配置到自己的app里面
CACHE_KEY_TEMPLATE = {}

# 定时任务
CELERY_BEAT_SCHEDULE = {}

# api服务监听端口，通过 python manage.py start all 命令启动时的监听端口
HTTP_LISTEN_PORT = 8896
