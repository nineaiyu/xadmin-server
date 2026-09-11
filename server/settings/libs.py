#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : libs
# author : ly_13
# date : 11/14/2024
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured

from .base import SECRET_KEY, CACHES, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, CELERY_BROKER_CACHE_ID
from ..const import CONFIG

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "common.swagger.utils.CustomAutoSchema",
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
        # 'common.drf.renders.CSVFileRenderer', # 为什么注释：因为导入导出需要权限判断，在导入导出功能中再次自定义解析数据
        # 'common.drf.renders.ExcelFileRenderer',
    ),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "common.drf.parsers.AxiosMultiPartParser",
        "common.drf.parsers.CSVFileParser",
        "common.drf.parsers.ExcelFileParser",
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "common.core.auth.CookieJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        # Basic 认证为 base64 明文凭证，仅限本地调试按需开启（BASIC_AUTH_ENABLED）
        *(["rest_framework.authentication.BasicAuthentication"] if CONFIG.BASIC_AUTH_ENABLED else []),
        # PAT 认证（机器集成凭证）：无 Pat 头静默跳过，带 Pat 头时按凭证认证
        "common.core.auth.PersonalAccessTokenAuthentication",
    ],
    "EXCEPTION_HANDLER": "common.core.exception.common_exception_handler",
    "DEFAULT_METADATA_CLASS": "common.drf.metadata.SimpleMetadataWithFilters",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        # PAT 凭证级限流：非 PAT 请求 get_cache_key 返回 None 直接放行
        "common.core.throttle.PatThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {  # {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
        "anon": "60/m",
        "user": "600/m",
        "upload": "100/m",
        "download1": "10/m",
        "download2": "100/h",
        "register": "50/d",
        "reset_password": "50/d",
        "login": "50/h",
        **CONFIG.DEFAULT_THROTTLE_RATES,
    },
    "DEFAULT_PAGINATION_CLASS": "common.core.pagination.PageNumber",
    "DEFAULT_PERMISSION_CLASSES": [
        # 'rest_framework.permissions.IsAuthenticated',
        # PAT scope 统一校验已内联在 IsAuthenticated.has_permission：
        # action 级 permission_classes 会整体替换默认链，独立权限类会被漏掉
        "common.core.permission.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "common.core.filter.BaseDataPermissionFilter",
    ),
    "DATETIME_FORMAT": "%Y-%m-%d %H:%M:%S",
    "DATETIME_INPUT_FORMATS": ["%Y/%m/%d %H:%M:%S", "iso-8601", "%Y-%m-%d %H:%M:%S"],
}

# DRF扩展缓存时间
REST_FRAMEWORK_EXTENSIONS = {
    # 缓存时间
    "DEFAULT_CACHE_RESPONSE_TIMEOUT": 3600,
    # 缓存存储
    "DEFAULT_USE_CACHE": "default",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(seconds=CONFIG.ACCESS_TOKEN_LIFETIME),
    "REFRESH_TOKEN_LIFETIME": timedelta(seconds=CONFIG.REFRESH_TOKEN_LIFETIME),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,  # 在登录的时候更新user表  last_login 字段
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUDIENCE": "x",
    "ISSUER": "server",
    "JWK_URL": None,
    "LEEWAY": 0,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
    "AUTH_TOKEN_CLASSES": ("common.core.auth.ServerAccessToken",),
    # 'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    "TOKEN_TYPE_CLAIM": "token_type",
    "TOKEN_USER_CLASS": "rest_framework_simplejwt.models.TokenUser",
    "JTI_CLAIM": "jti",
    "SLIDING_TOKEN_REFRESH_EXP_CLAIM": "refresh_exp",
    "SLIDING_TOKEN_LIFETIME": timedelta(minutes=5),
    "SLIDING_TOKEN_REFRESH_LIFETIME": timedelta(days=1),
}

CORS_ALLOW_CREDENTIALS = True
# 生产环境请在 config.yml 配置 CORS_ALLOWED_ORIGINS 白名单；
# 同源部署（nginx 反代）不受 CORS 影响，无需配置。
# 确需全放行时显式设置 CORS_ALLOW_ALL_ORIGINS: true（带凭证全放行属高危配置，不推荐）
CORS_ALLOW_ALL_ORIGINS = CONFIG.CORS_ALLOW_ALL_ORIGINS
CORS_ALLOWED_ORIGINS = CONFIG.CORS_ALLOWED_ORIGINS

if CORS_ALLOW_ALL_ORIGINS and CORS_ALLOW_CREDENTIALS:
    # 「带凭证 + 任意源放行」等价于允许任意站点携带登录态调用 API（凭证外泄/CSRF 面），
    # 属高危组合：直接拒绝启动，强制改用 CORS_ALLOWED_ORIGINS 白名单
    raise ImproperlyConfigured(
        "CORS_ALLOW_ALL_ORIGINS 与 CORS_ALLOW_CREDENTIALS 不能同时开启："
        "请配置 CORS_ALLOWED_ORIGINS 域名白名单，或将 CORS_ALLOW_ALL_ORIGINS 置为 false。"
    )

CORS_ALLOW_METHODS = (
    "DELETE",
    "GET",
    "OPTIONS",
    "POST",
    "PUT",
    "PATCH",
)

CORS_ALLOW_HEADERS = (
    "XMLHttpRequest",
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-token",
)

# Celery Configuration Options
# https://docs.celeryq.dev/en/stable/userguide/configuration.html?
CELERY_TIMEZONE = CONFIG.TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

# 队列路由：heavy 队列承载导入/导出/批量操作等重任务（background_task_view_set_job），
# 避免慢任务阻塞邮件/短信/站内信等轻量任务；worker 由 start celery_heavy 拉起消费 heavy 队列
CELERY_TASK_ROUTES = {
    "common.tasks.background_task_view_set_job": {"queue": "heavy"},
    # Office 转 PDF 预览（ADR-013）：CPU 密集型外部进程，禁止占用默认队列
    "system.tasks.convert_office_preview_task": {"queue": "heavy"},
}

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# CELERY_RESULT_BACKEND = ''
# CELERY_CACHE_BACKEND = 'django-cache'

CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"

# broker redis
DJANGO_DEFAULT_CACHES = CACHES["default"]
CELERY_BROKER_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{CELERY_BROKER_CACHE_ID}"

# CELERY_WORKER_CONCURRENCY = 10  # worker并发数
# worker 自动扩缩容区间（最大/最小池进程数）；
# 注意：当前 worker 启动命令未带 --autoscale（见 common/management/commands/services/services/celery_base.py），
# 该配置暂不生效，如需启用请同步修改启动命令
CELERY_WORKER_AUTOSCALE = [10, 3]

CELERYD_FORCE_EXECV = True  # 非常重要,有些情况下可以防止死
CELERY_RESULT_EXPIRES = 3600 * 24 * 7  # 任务结果过期时间

# 任务执行历史（TaskExecution/TaskResult）及日志保留天数
TASK_EXECUTION_KEEP_DAYS = int(CONFIG.get("TASK_EXECUTION_KEEP_DAYS", 30))

CELERY_WORKER_DISABLE_RATE_LIMITS = True  # 任务发出后，经过一段时间还未收到acknowledge , 就将任务重新交给其他worker执行
# 预取须与并发量级匹配：60（≈6 倍并发）会让单 worker 囤积大量任务，
# worker 异常退出时这些任务会被大面积重投
CELERY_WORKER_PREFETCH_MULTIPLIER = 10

# 软超时：到达后先抛 SoftTimeLimitExceeded 让任务优雅收尾（清理临时文件/回写状态），
# 再由 CELERY_TASK_TIME_LIMIT（30min 硬超时）强制终止
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60

CELERY_WORKER_MAX_TASKS_PER_CHILD = 200  # 每个worker执行了多少任务就会死掉，我建议数量可以大一些，比如200

CELERY_ENABLE_UTC = False
DJANGO_CELERY_BEAT_TZ_AWARE = True

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"

# celery消息的序列化方式，由于要把对象当做参数所以使用pickle
# CELERY_RESULT_SERIALIZER = 'pickle'
# CELERY_ACCEPT_CONTENT = ['pickle']
# CELERY_TASK_SERIALIZER = 'pickle'


SPECTACULAR_SETTINGS = {
    "TITLE": "Xadmin Server API",
    "DESCRIPTION": "Django Xadmin Server",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PUBLIC": False,
    "SWAGGER_UI_DIST": "SIDECAR",  # shorthand to use the sidecar instead
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SWAGGER_UI_SETTINGS": {
        "displayRequestDuration": True,
        "deepLinking": True,
        "filter": True,
        "persistAuthorization": True,
        "displayOperationId": False,
    },
    "SERIALIZER_EXTENSIONS": [
        "common.swagger.utils.OpenApiAuthenticationScheme",
        "common.swagger.utils.OpenApiPrimaryKeyRelatedField",
        "common.swagger.utils.LabeledChoiceFieldExtension",
    ],
    # 'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
}
