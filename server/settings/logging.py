#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logging
# author : ly_13
# date : 10/18/2024
import os
from .base import *

BASE_LOG_DIR = os.path.join(BASE_DIR, "logs", "api")
TMP_LOG_DIR = os.path.join(BASE_DIR, "logs", "tmp")
CELERY_LOG_DIR = os.path.join(BASE_DIR, "logs", "task")
LOG_LEVEL = locals().get('LOG_LEVEL', "DEBUG")

if not os.path.isdir(BASE_LOG_DIR):
    os.makedirs(BASE_LOG_DIR)
if not os.path.isdir(TMP_LOG_DIR):
    os.makedirs(TMP_LOG_DIR)
if not os.path.isdir(CELERY_LOG_DIR):
    os.makedirs(CELERY_LOG_DIR)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[%(filename)s:%(funcName)s:%(lineno)d %(levelname)s] %(asctime)s %(process)d %(thread)d %(message)s'
        },
        'main': {
            'datefmt': '%Y-%m-%d %H:%M:%S',
            'format': '%(asctime)s [%(filename)s:%(funcName)s:%(lineno)d %(levelname)s] %(message)s',
        },
        'exception': {
            'datefmt': '%Y-%m-%d %H:%M:%S',
            'format': '\n%(asctime)s [%(levelname)s] %(message)s',
        },
        'simple': {
            'format': '[%(levelname)s][%(asctime)s][%(filename)s:%(funcName)s:%(lineno)d]%(message)s'
        },
    },
    'filters': {
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'null': {
            'level': 'DEBUG',
            'class': 'logging.NullHandler',
        },
        'console': {
            'level': 'DEBUG',
            'filters': ['require_debug_true'],  # 只有在Django debug为True时才在屏幕打印日志
            'class': 'logging.StreamHandler',
            'formatter': 'main'
        },
        'server': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',  # 保存到文件，根据时间自动切
            'filename': os.path.join(BASE_LOG_DIR, "server.log"),  # 日志文件
            'maxBytes': 1024 * 1024 * 100,  # 日志大小 100M
            'backupCount': 10,  # 备份数为3
            # 'when': 'W6',  # 每天一切， 可选值有S/秒 M/分 H/小时 D/天 W0-W6/周(0=周一) midnight/如果没指定时间就默认在午夜
            'formatter': 'main',
            'encoding': 'utf-8',
        },
        'drf_exception': {
            'encoding': 'utf8',
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'exception',
            'maxBytes': 1024 * 1024 * 100,
            'backupCount': 7,
            'filename': os.path.join(BASE_LOG_DIR, "drf_exception.log"),
        },
        'unexpected_exception': {
            'encoding': 'utf8',
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'exception',
            'maxBytes': 1024 * 1024 * 100,
            'backupCount': 7,
            'filename': os.path.join(BASE_LOG_DIR, "unexpected_exception.log"),
        },
        'sql': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',  # 保存到文件，自动切
            'filename': os.path.join(BASE_LOG_DIR, "sql.log"),  # 日志文件
            'maxBytes': 1024 * 1024 * 50,  # 日志大小 50M
            'backupCount': 10,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        '': {  # 默认的logger应用如下配置
            'handlers': ['server', 'console', 'drf_exception', 'unexpected_exception'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'django': {
            'handlers': ['null'],
            'propagate': False,
            'level': 'DEBUG',
        },
        'django.request': {
            'handlers': ['console', 'server'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console', 'server'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['sql'],
            'propagate': True,
            'level': 'DEBUG',
        },
        'drf_exception': {
            'handlers': ['console', 'drf_exception'],
            'level': 'ERROR',
        },
        'unexpected_exception': {
            'handlers': ['console', 'unexpected_exception'],
            'level': 'ERROR',
        },
    },
}