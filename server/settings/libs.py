#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : libs
# author : ly_13
# date : 11/14/2024
from datetime import timedelta

from .base import SECRET_KEY, CACHES, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, CELERY_BROKER_CACHE_ID
from ..const import CONFIG

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'common.swagger.utils.CustomAutoSchema',
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
        # 'common.drf.renders.CSVFileRenderer', # 为什么注释：因为导入导出需要权限判断，在导入导出功能中再次自定义解析数据
        # 'common.drf.renders.ExcelFileRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'common.drf.parsers.AxiosMultiPartParser',
        'common.drf.parsers.CSVFileParser',
        'common.drf.parsers.ExcelFileParser',
    ),
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'common.core.auth.CookieJWTAuthentication',
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",  # 允许basic授权，方便调试使用
    ],
    'EXCEPTION_HANDLER': 'common.core.exception.common_exception_handler',
    'DEFAULT_METADATA_CLASS': 'common.drf.metadata.SimpleMetadataWithFilters',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {  # {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
        'anon': '60/m',
        'user': '600/m',
        'upload': '100/m',
        'download1': '10/m',
        'download2': '100/h',
        'register': '50/d',
        'reset_password': '50/d',
        'login': '50/h',
        **CONFIG.DEFAULT_THROTTLE_RATES
    },
    'DEFAULT_PAGINATION_CLASS': 'common.core.pagination.PageNumber',
    'DEFAULT_PERMISSION_CLASSES': [
        # 'rest_framework.permissions.IsAuthenticated',
        'common.core.permission.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.OrderingFilter',
        'common.core.filter.BaseDataPermissionFilter',
    ),
    'DATETIME_FORMAT': '%Y-%m-%d %H:%M:%S',
    'DATETIME_INPUT_FORMATS': ['%Y/%m/%d %H:%M:%S', 'iso-8601', '%Y-%m-%d %H:%M:%S'],
}

# DRF扩展缓存时间
REST_FRAMEWORK_EXTENSIONS = {
    # 缓存时间
    'DEFAULT_CACHE_RESPONSE_TIMEOUT': 3600,
    # 缓存存储
    'DEFAULT_USE_CACHE': 'default',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(seconds=CONFIG.ACCESS_TOKEN_LIFETIME),
    'REFRESH_TOKEN_LIFETIME': timedelta(seconds=CONFIG.REFRESH_TOKEN_LIFETIME),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,  # 在登录的时候更新user表  last_login 字段

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUDIENCE': 'x',
    'ISSUER': 'server',
    'JWK_URL': None,
    'LEEWAY': 0,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'rest_framework_simplejwt.authentication.default_user_authentication_rule',

    'AUTH_TOKEN_CLASSES': ('common.core.auth.ServerAccessToken',),
    # 'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'TOKEN_USER_CLASS': 'rest_framework_simplejwt.models.TokenUser',

    'JTI_CLAIM': 'jti',

    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOW_METHODS = (
    'DELETE',
    'GET',
    'OPTIONS',
    'POST',
    'PUT',
    'PATCH',
)

CORS_ALLOW_HEADERS = (
    'XMLHttpRequest',
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-token"
)

# Celery Configuration Options
# https://docs.celeryq.dev/en/stable/userguide/configuration.html?
CELERY_TIMEZONE = CONFIG.TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# CELERY_RESULT_BACKEND = ''
# CELERY_CACHE_BACKEND = 'django-cache'

CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'default'

# broker redis
DJANGO_DEFAULT_CACHES = CACHES['default']
CELERY_BROKER_URL = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{CELERY_BROKER_CACHE_ID}'

# CELERY_WORKER_CONCURRENCY = 10  # worker并发数
CELERY_WORKER_AUTOSCALE = [10, 3]  # which needs two numbers: the maximum and minimum number of pool processes

CELERYD_FORCE_EXECV = True  # 非常重要,有些情况下可以防止死
CELERY_RESULT_EXPIRES = 3600 * 24 * 7  # 任务结果过期时间

CELERY_WORKER_DISABLE_RATE_LIMITS = True  # 任务发出后，经过一段时间还未收到acknowledge , 就将任务重新交给其他worker执行
CELERY_WORKER_PREFETCH_MULTIPLIER = 60  # celery worker 每次去redis取任务的数量

CELERY_WORKER_MAX_TASKS_PER_CHILD = 200  # 每个worker执行了多少任务就会死掉，我建议数量可以大一些，比如200

CELERY_ENABLE_UTC = False
DJANGO_CELERY_BEAT_TZ_AWARE = True

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

# celery消息的序列化方式，由于要把对象当做参数所以使用pickle
# CELERY_RESULT_SERIALIZER = 'pickle'
# CELERY_ACCEPT_CONTENT = ['pickle']
# CELERY_TASK_SERIALIZER = 'pickle'


SPECTACULAR_SETTINGS = {
    'TITLE': 'Xadmin Server API',
    'DESCRIPTION': 'Django Xadmin Server',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SERVE_PUBLIC': False,
    'SWAGGER_UI_DIST': 'SIDECAR',  # shorthand to use the sidecar instead
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
    "SWAGGER_UI_SETTINGS": {
        "displayRequestDuration": True,
        "deepLinking": True,
        "filter": True,
        "persistAuthorization": True,
        "displayOperationId": False,
    },
    # 'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
}
