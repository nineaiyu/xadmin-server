#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : libs
# author : ly_13
# date : 11/14/2024
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured

from common.celery.routing import celery_task_route

from ..const import CONFIG
from .base import CACHES, CELERY_BROKER_CACHE_ID, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT, SECRET_KEY

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
        # 文档站登录（/api-docs/login/）：与主登录同口径接入账号锁定之外的单列
        # 更严限流，避免该入口被用作口令爆破通道
        "api_docs_login": "10/m",
        # 开放平台 client-credentials 换发（/api/system/open/token）：按 client
        # 维度防 client_secret 在线爆破与换发风暴（换发即轮换，频繁调用等于凭证写放大）
        "open_client": "20/m",
        # OAuth token/revoke（/api/system/open/oauth/token|revoke）：按 client
        # 维度收敛；多用户共用同一应用的后端调用，速率须覆盖正常登录高峰
        "oauth_client": "120/m",
        # 发送验证码（/api/system/auth/verify）：IP 维度防短信/邮件轰炸与
        # Redis 写放大；按目标计数的锁定由 SendVerifyCodeBlockUtil 兜底（互补维度）
        "verify_code": "20/m",
        # 临时令牌（/api/system/auth/token）：每次调用强制生成新缓存令牌
        # 收紧到低于全局匿名档
        "temp_token": "30/m",
        # AI 对话类端点（问答/NL/受限动作/聊天室 AI，含流式）：LLM 外呼是最高成本入口，
        # 按用户维度防突发（人工问答远低于该值；批量跑批/多浏览器回归不触顶）
        "ai_chat": "120/m",
        # AI 管理类重操作（连接测试/档案探测/知识库同步/向量构建）
        "ai_admin": "10/m",
        # 导出/导入重 IO 端点（export-data/-async、import-*、下载中心 download）：
        # 按用户维度防突发提交/下载风暴（并发上限另有 EXPORT_ASYNC_MAX_RUNNING 兜底）
        "export_import": "30/m",
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
    # 密钥分离（3.4）：显式配置 JWT_SIGNING_KEY 后与 Django SECRET_KEY 解耦，
    # 轮换 SECRET_KEY（会话/CSRF）不再连带踢掉全部登录态；留空沿用 SECRET_KEY
    "SIGNING_KEY": CONFIG.JWT_SIGNING_KEY or SECRET_KEY,
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

# 会话存储：cached_db（Redis 读 + DB 写穿）。
# 默认 DB 会话在「请求携带 sessionid」时每请求多一次库读（DRF 认证链含
# SessionAuthentication，取 user 会加载会话）；cached_db 把读路径收敛到 Redis，
# 写路径仍落库（Redis 清空/驱逐不丢会话），多副本部署下会话天然共享。
# 会话 cookie 的安全属性见 server/settings/security_https.py（SESSION_COOKIE_SECURE）。
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# 密码哈希：argon2id 优先（抗 GPU/ASIC，内存硬），存量 PBKDF2-SHA256 校验仍走原 hasher。
# 渐进迁移：AbstractBaseUser.check_password 自带 setter——登录校验通过且存储哈希
# 不是首选 hasher 时自动以新 hasher 重哈希落库，无需批量迁移；新设密码一律 argon2id。
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

CORS_ALLOW_CREDENTIALS = True
# 生产环境请在 config.yml 配置 CORS_ALLOWED_ORIGINS 白名单；
# 同源部署（nginx 反代）不受 CORS 影响，无需配置。
# 确需全放行时显式设置 CORS_ALLOW_ALL_ORIGINS: true（带凭证全放行属高危配置，不推荐）
CORS_ALLOW_ALL_ORIGINS = CONFIG.CORS_ALLOW_ALL_ORIGINS
CORS_ALLOWED_ORIGINS = CONFIG.CORS_ALLOWED_ORIGINS

# CSRF 受信源：Django 4+ 要求带 scheme 的完整源；跨域部署下若只配了 CORS 白名单，
# 会话态（admin / 表单）的 POST 仍会被 CSRF 拒绝——默认沿用 CORS 白名单，
# 需要独立白名单时在 config.yml 配 CSRF_TRUSTED_ORIGINS（两者语义都是「可信前端源」）。
CSRF_TRUSTED_ORIGINS = CONFIG.CSRF_TRUSTED_ORIGINS or list(CORS_ALLOWED_ORIGINS)

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
# 避免慢任务阻塞邮件/短信/站内信等轻量任务；worker 由 start celery_heavy 拉起消费 heavy 队列。
# 采用可调用路由（common/celery/routing.py）：内置表兜底 + 各应用 config.py::TASK_ROUTES
# 声明优先，二开应用改队列归属无需修改本工程层文件
CELERY_TASK_ROUTES = celery_task_route

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# CELERY_RESULT_BACKEND = ''
# CELERY_CACHE_BACKEND = 'django-cache'

CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"

# broker redis
DJANGO_DEFAULT_CACHES = CACHES["default"]
CELERY_BROKER_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{CELERY_BROKER_CACHE_ID}"

# CELERY_WORKER_CONCURRENCY = 10  # worker并发数

CELERY_RESULT_EXPIRES = 3600 * 24 * 7  # 任务结果过期时间

# 任务执行历史（TaskExecution/TaskResult）及日志保留天数
TASK_EXECUTION_KEEP_DAYS = int(CONFIG.get("TASK_EXECUTION_KEEP_DAYS", 30))

CELERY_WORKER_DISABLE_RATE_LIMITS = True  # 任务发出后，经过一段时间还未收到acknowledge , 就将任务重新交给其他worker执行
# 预取倍数（默认队列的 worker）：与并发量级匹配即可，取 4。
# 取值口径：默认 worker 线程池并发 = CELERY_WORKER_COUNT（默认 4，见 conf/defaults.py），
# 预取 4 倍并发在「长任务排队」与「worker 崩溃重投」之间取平衡——
# 过大（如历史注释里的 60）会让单 worker 囤积大量未确认任务，
# worker 异常退出时这些任务被大面积重投；heavy 队列则由
# services/celery_heavy.py 固定 prefetch=1（长任务串行消费）。
CELERY_WORKER_PREFETCH_MULTIPLIER = 4

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
