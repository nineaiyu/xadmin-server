#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 6/11/2024

# debug为false的时候，如果遇到静态文件无法访问，比如api文档无法正常打开，需要通过下面命令收集静态文件
# python manage.py collectstatic

DEBUG = False

ALLOWED_HOSTS = ["*"]

# SECURITY WARNING: keep the secret key used in production secret!
# 加密密钥 生产服必须保证唯一性，你必须保证这个值的安全，否则攻击者可以用它来生成自己的签名值
# $ cat /dev/urandom | tr -dc A-Za-z0-9 | head -c 49;echo
SECRET_KEY = 'django-insecure-mlq6(#a^2vk!1=7=xhp#$i=o5d%namfs=+b26$m#sh_2rco7j^'

### 更多数据库配置，参考官方文档：https://docs.djangoproject.com/zh-hans/5.0/ref/databases/

# # mysql 数据库配置
# # create database xadmin default character set utf8 COLLATE utf8_general_ci;
# # grant all on xadmin.* to server@'127.0.0.1' identified by 'KGzKjZpWBp4R4RSa';
DB_ENGINE = 'django.db.backends.mysql'
DB_HOST = 'mariadb'
DB_PORT = 3306
DB_USER = 'server'
DB_DATABASE = 'xadmin'
DB_PASSWORD = 'KGzKjZpWBp4R4RSa'
DB_OPTIONS = {'init_command': 'SET sql_mode="STRICT_TRANS_TABLES"', 'charset': 'utf8mb4'}


# sqlite3 配置，和 mysql配置 二选一, 默认sqlite数据库
# DB_ENGINE = 'django.db.backends.sqlite3'

# 缓存配置
REDIS_HOST = "redis"
REDIS_PORT = 6379
REDIS_PASSWORD = "nineven"

# 需要将创建的应用写到里面
XADMIN_APPS = []

# 速率限制配置
DEFAULT_THROTTLE_RATES = {}

# redis key，建议开发的时候，配置到自己的app里面
CACHE_KEY_TEMPLATE = {}

# 定时任务
CELERY_BEAT_SCHEDULE = {}

# api服务监听端口，通过 python manage.py start all 命令启动时的监听端口
HTTP_LISTEN_PORT = 8896
GUNICORN_MAX_WORKER = 4 # API服务最多启动的worker数量
