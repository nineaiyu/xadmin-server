#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logging
# author : ly_13
# date : 10/18/2024

from .base import *

LOG_DIR = os.path.join(BASE_DIR, "logs")
TMP_LOG_DIR = os.path.join(BASE_DIR, "logs", "tmp")
CELERY_LOG_DIR = os.path.join(BASE_DIR, "logs", "task")
LOG_LEVEL = locals().get('LOG_LEVEL', "DEBUG")
DEBUG_DEV = locals().get('DEBUG_DEV', False)

SERVER_LOG_FILE = os.path.join(LOG_DIR, 'server.log')
DRF_EXCEPTION_LOG_FILE = os.path.join(LOG_DIR, 'drf_exception.log')
UNEXPECTED_EXCEPTION_LOG_FILE = os.path.join(LOG_DIR, 'unexpected_exception.log')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '%(levelname)s %(asctime)s %(pathname)s:%(lineno)d  %(message)s'
        },
        'main': {
            'datefmt': '%Y-%m-%d %H:%M:%S',
            'format': '%(asctime)s [%(levelname).4s] %(message)s',
        },
        'exception': {
            'datefmt': '%Y-%m-%d %H:%M:%S',
            'format': '\n%(asctime)s [%(levelname)s] %(message)s',
        },
        'simple': {
            'format': '%(levelname)s %(message)s'
        },
    },
    'handlers': {
        'null': {
            'level': 'DEBUG',
            'class': 'logging.NullHandler',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'main'
        },
        'server': {
            'encoding': 'utf8',
            'level': 'DEBUG',
            'class': 'server.logging.DailyTimedRotatingFileHandler',
            'when': 'midnight',
            'formatter': 'main',
            'filename': SERVER_LOG_FILE,
        },
        'drf_exception': {
            'encoding': 'utf8',
            'level': 'DEBUG',
            'class': 'server.logging.DailyTimedRotatingFileHandler',
            'when': 'midnight',
            'formatter': 'exception',
            'filename': DRF_EXCEPTION_LOG_FILE,
        },
        'unexpected_exception': {
            'encoding': 'utf8',
            'level': 'DEBUG',
            'class': 'server.logging.DailyTimedRotatingFileHandler',
            'when': 'midnight',
            'formatter': 'exception',
            'filename': UNEXPECTED_EXCEPTION_LOG_FILE,
        }
    },
    'loggers': {
        # '': {  # 默认的logger应用如下配置
        #     'handlers': ['server', 'console', 'drf_exception', 'unexpected_exception'],
        #     'level': LOG_LEVEL,
        #     'propagate': True,
        # },
        'django': {
            'handlers': ['null'],
            'propagate': False,
            'level': LOG_LEVEL,
        },
        'django.request': {
            'handlers': ['console', 'server'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console', 'server'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'xadmin': {
            'handlers': ['console', 'server'],
            'level': LOG_LEVEL,
        },
        'drf_exception': {
            'handlers': ['console', 'drf_exception'],
            'level': LOG_LEVEL,
        },
        'unexpected_exception': {
            'handlers': ['unexpected_exception'],
            'level': LOG_LEVEL,
        },
    },
}

if DEBUG_DEV:
    LOGGING['loggers']['django.db'] = {
        'handlers': ['console', 'server'],
        'level': 'DEBUG'
    }

if not os.path.isdir(LOG_DIR):
    os.makedirs(LOG_DIR)
if not os.path.isdir(TMP_LOG_DIR):
    os.makedirs(TMP_LOG_DIR)
if not os.path.isdir(CELERY_LOG_DIR):
    os.makedirs(CELERY_LOG_DIR)
