#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logging
# author : ly_13
# date : 10/18/2024
import os

from ..const import LOG_DIR, CONFIG, TMP_DIR, CELERY_LOG_DIR

SERVER_LOG_FILE = os.path.join(LOG_DIR, "server.log")
DRF_EXCEPTION_LOG_FILE = os.path.join(LOG_DIR, "drf_exception.log")
UNEXPECTED_EXCEPTION_LOG_FILE = os.path.join(LOG_DIR, "unexpected_exception.log")
LOG_LEVEL = CONFIG.LOG_LEVEL
# LOG_FORMAT=text（默认，人类可读）/ json（结构化，供采集端解析）
FILE_FORMATTER = "json" if str(CONFIG.LOG_FORMAT).lower() == "json" else "main"
# 按天滚动的历史日志保留天数（0 表示不清理）
LOG_BACKUP_COUNT = int(CONFIG.LOG_BACKUP_COUNT or 0)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "()": "server.logging.ServerFormatter",
            "format": "%(asctime)s.%(msecs)03d [%(requestUuid)s %(levelname)s] [%(requestUser)s] [%(pathname)s:%(lineno)d] %(process)d %(thread)d %(message)s",
        },
        "main": {
            "()": "server.logging.ServerFormatter",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "format": "%(asctime)s.%(msecs)03d [%(requestUuid)s %(levelname).4s] [%(requestUser)s] [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        },
        "exception": {
            "()": "server.logging.ServerFormatter",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "format": "\n%(asctime)s.%(msecs)03d [%(requestUuid)s %(levelname)s %(requestUser)s] %(message)s",
        },
        "json": {
            "()": "server.logging.JsonFormatter",
        },
        "simple": {"format": "%(levelname)s %(message)s"},
    },
    "handlers": {
        "null": {
            "level": "DEBUG",
            "class": "logging.NullHandler",
        },
        "console": {"level": "DEBUG", "class": "server.logging.ColorHandler", "formatter": FILE_FORMATTER},
        "server": {
            "encoding": "utf8",
            "level": "DEBUG",
            "class": "server.logging.DailyTimedRotatingFileHandler",
            "when": "midnight",
            "formatter": FILE_FORMATTER,
            "filename": SERVER_LOG_FILE,
            "backupCount": LOG_BACKUP_COUNT,
        },
        "drf_exception": {
            "encoding": "utf8",
            "level": "DEBUG",
            "class": "server.logging.DailyTimedRotatingFileHandler",
            "when": "midnight",
            "formatter": "exception" if FILE_FORMATTER == "main" else FILE_FORMATTER,
            "filename": DRF_EXCEPTION_LOG_FILE,
            "backupCount": LOG_BACKUP_COUNT,
        },
        "unexpected_exception": {
            "encoding": "utf8",
            "level": "DEBUG",
            "class": "server.logging.DailyTimedRotatingFileHandler",
            "when": "midnight",
            "formatter": "exception" if FILE_FORMATTER == "main" else FILE_FORMATTER,
            "filename": UNEXPECTED_EXCEPTION_LOG_FILE,
            "backupCount": LOG_BACKUP_COUNT,
        },
    },
    "loggers": {
        # '': {  # 默认的logger ，所有日志将会输出到配置的 handlers
        #     'handlers': ['server', 'console'],
        #     'level': LOG_LEVEL,
        #     'propagate': False,
        # },
        "django": {
            "handlers": ["null"],
            "propagate": False,
            "level": LOG_LEVEL,
        },
        "django.request": {
            "handlers": ["console", "server"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console", "server"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console", "server"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xadmin": {
            "handlers": ["console", "server"],
            "level": LOG_LEVEL,
        },
        "drf_exception": {
            "handlers": ["console", "drf_exception"],
            "level": LOG_LEVEL,
        },
        "unexpected_exception": {
            "handlers": ["console", "unexpected_exception"],
            "level": LOG_LEVEL,
        },
    },
}

if CONFIG.DEBUG_DEV:
    LOGGING["loggers"]["django.db"] = {"handlers": ["console", "server"], "level": "DEBUG"}

if not os.path.isdir(LOG_DIR):
    os.makedirs(LOG_DIR)
if not os.path.isdir(TMP_DIR):
    os.makedirs(TMP_DIR)
if not os.path.isdir(CELERY_LOG_DIR):
    os.makedirs(CELERY_LOG_DIR)
