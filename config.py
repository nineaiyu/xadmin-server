#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 6/11/2024
import os
import json


def read_env():
    """读取 .env 文件的函数"""
    env_dict = {}
    try:
        with open('.env') as f:
            content = f.read()
        for line in content.splitlines():
            if line.startswith('#') or not line.strip():
                continue
            key, value = line.split('=', 1)
            env_dict[key] = value
    except IOError:
        pass
    # print('读取到本地.env配置:', env_dict)
    # 将字典中的环境变量设置到 os.environ 中
    for key, value in env_dict.items():
        os.environ.setdefault(key, value)


# 判断是否在本地开发环境。打包成镜像时项目代码将不含.env文件,可通过docker-compose.yml文件的environment或env_file配置
if os.path.exists('.env'):
    read_env()


# DEBUG 模式 开启DEBUG后遇到错误时可以看到更多日志
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# 用于debug模式下，输出sql日志
DEBUG_DEV = os.getenv('DEBUG_DEV', 'False').lower() == 'true'

# DEBUG, INFO, WARNING, ERROR, CRITICAL can set
# 日志级别
LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING')

ALLOWED_HOSTS = json.loads(os.getenv('ALLOWED_HOSTS', '["*"]'))

# SECURITY WARNING: keep the secret key used in production secret!
# 加密密钥 生产服必须保证唯一性，你必须保证这个值的安全，否则攻击者可以用它来生成自己的签名值
# $ cat /dev/urandom | tr -dc A-Za-z0-9 | head -c 49;echo
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-mlq6(#a^2vk!1=7=xhp#$i=o5d%namfs=+b26$m#sh_2rco7j^')

### 更多数据库配置，参考官方文档：https://docs.djangoproject.com/zh-hans/5.0/ref/databases/

# # mysql 数据库配置
# # create database xadmin default character set utf8mb4 COLLATE utf8mb4_bin;
# # grant all on xadmin.* to server@'127.0.0.1' identified by 'KGzKjZpWBp4R4RSa';
DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.mysql')
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_PORT = int(os.getenv('DB_PORT', '3306'))
DB_USER = os.getenv('DB_USER', 'server')
DB_DATABASE = os.getenv('DB_DATABASE', 'xadmin')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'KGzKjZpWBp4R4RSa')
DB_OPTIONS = json.loads(os.getenv('DB_OPTIONS', '{"init_command": "SET sql_mode=\'STRICT_TRANS_TABLES\'", "charset": "utf8mb4", "collation": "utf8mb4_bin"}'))


# sqlite3 配置，和 mysql配置 二选一, 默认mysql数据库
# DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.sqlite3')

# 缓存配置
REDIS_HOST = os.getenv('REDIS_HOST', "redis")
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', "nineven")

# 需要将创建的应用写到里面
XADMIN_APPS = json.loads(os.getenv('XADMIN_APPS', '[]'))

# 速率限制配置
DEFAULT_THROTTLE_RATES = json.loads(os.getenv('DEFAULT_THROTTLE_RATES', '{}'))

# redis key，建议开发的时候，配置到自己的app里面
CACHE_KEY_TEMPLATE = json.loads(os.getenv('CACHE_KEY_TEMPLATE', '{}'))

# 定时任务
CELERY_BEAT_SCHEDULE = json.loads(os.getenv('CELERY_BEAT_SCHEDULE', '{}'))

# api服务监听端口，通过 python manage.py start all 命令启动时的监听端口
HTTP_LISTEN_PORT = int(os.getenv('HTTP_LISTEN_PORT', '8896'))
GUNICORN_MAX_WORKER = int(os.getenv('GUNICORN_MAX_WORKER', '4'))  # API服务最多启动的worker数量
