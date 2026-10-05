"""Django settings for server project（Django 核心设置层）。

框架/库级配置（DRF / JWT / CORS / 会话与密码哈希 / Celery）见 libs.py；
HTTPS 与安全响应头覆盖见 security_https.py；Redis 缓存与 Channels 通道层见
cache_channel.py；数据库连接配置见 databases.py（均经 star-import 并入本模块）。
参考：https://docs.djangoproject.com/en/stable/ref/settings/
"""

import os

from django.core.exceptions import ImproperlyConfigured

from ..const import CONFIG, PROJECT_DIR

BASE_DIR = PROJECT_DIR
# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = CONFIG.SECRET_KEY

# 密钥分离（3.4）：字段加密主密钥与历史密钥清单（JWT 侧见 libs.py 的 SIMPLE_JWT.SIGNING_KEY）。
# 留空 = 沿用 SECRET_KEY（旧行为）；显式配置后字段加密与 SECRET_KEY 解耦，可独立轮换，
# 轮换期把旧密钥放入 FIELD_ENCRYPTION_LEGACY_KEYS 即可读出存量密文（写新读旧）
FIELD_ENCRYPTION_KEY = CONFIG.FIELD_ENCRYPTION_KEY
FIELD_ENCRYPTION_LEGACY_KEYS = CONFIG.FIELD_ENCRYPTION_LEGACY_KEYS

DEBUG = CONFIG.DEBUG

# SECRET_KEY 同时作为 JWT 签名密钥（SIMPLE_JWT.SIGNING_KEY），
# 生产环境为空会导致任意伪造 token，直接拒绝启动；DEBUG 模式允许为空便于本地调试。
# 注：无配置文件回落 config_example.yml、DEBUG=true 或显式 SECRET_KEY_AUTO_GENERATE=true
# 三种场景会在 server/conf/manager.py 自动生成并持久化密钥（data/.secret_key），一般不会走到本分支。
if not SECRET_KEY and not DEBUG:
    raise ImproperlyConfigured(
        "SECRET_KEY is required when DEBUG is disabled. Fix it with one of:\n"
        "  1. Set SECRET_KEY in config.yml (recommended for production);\n"
        "  2. Export the SECRET_KEY environment variable before startup;\n"
        "  3. Set SECRET_KEY_AUTO_GENERATE=true to auto-generate and persist data/.secret_key."
    )

# SECURITY WARNING: If you run with debug turned on, more debug msg with be log
DEBUG_DEV = CONFIG.DEBUG_DEV

LOG_LEVEL = CONFIG.LOG_LEVEL

# 如果前端是代理，则可以通过该配置，在系统构建url的时候，获取正确的 scheme
# 需要在 前端加入该配置  proxy_set_header X-Forwarded-Proto $scheme;
# https://docs.djangoproject.com/zh-hans/4.2/ref/settings/#std-setting-SECURE_PROXY_SSL_HEADER
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 为了兼容前端NGINX代理，并且使用非80，443端口访问，详细查看源码：django.http.request.HttpRequest._get_raw_host
# https://docs.djangoproject.com/zh-hans/4.2/ref/settings/#use-x-forwarded-host
USE_X_FORWARDED_HOST = True

# DEBUG 模式默认放行所有 Host；生产环境必须通过 config.yml 配置域名白名单
ALLOWED_HOSTS = CONFIG.ALLOWED_HOSTS or (["*"] if DEBUG else [])

# 反向代理信任清单（防 XFF 伪造，语义见 conf.py）：默认空 = 不信任任何 X-Forwarded-For
TRUSTED_PROXY_IPS = CONFIG.TRUSTED_PROXY_IPS

# HTTPS 部署安全头拆分至 security_https.py（文件行数门禁），经 star-import 并入本模块
from .security_https import *  # noqa: F401,F403

# Application definition
XADMIN_APPS = CONFIG.XADMIN_APPS

# 功能模块裁剪（软裁剪）：MODULE_PRESET 选基线（core/standard/full），
# MODULE_ENABLE / MODULE_DISABLE 做显式增删。模块清单、依赖校验与裁剪动作
# 见 common/core/modules.py（默认 full = 全部开启，行为与改造前一致）
MODULE_PRESET = CONFIG.MODULE_PRESET
MODULE_ENABLE = CONFIG.MODULE_ENABLE
MODULE_DISABLE = CONFIG.MODULE_DISABLE

# 表前缀设置
# 1.指定配置
# DB_PREFIX={
# 'system': 'abc_', # system app所有的表都加前缀 abc_
# 'system.config':'xxa_', # 仅system.config添加前缀 xxa_
# '': '', # 默认前缀
# }
#
# 2.全局配置
# DB_PREFIX='abc_'  : 所有表都添加 abc_
DB_PREFIX = CONFIG.DB_PREFIX

# 应用与中间件装配拆分至 apps.py（文件行数门禁），装配本体与注释见该模块
from .apps import build_installed_apps, build_middleware  # noqa: E402

INSTALLED_APPS = build_installed_apps(XADMIN_APPS)
MIDDLEWARE = build_middleware()

# CSP 策略（S3 落地，独立于 Office 预览）：django-csp 生成，运行期模式由
# CSPModeMiddleware + SysConfig.CSP_MODE 决定（默认 report-only 观察，再切 enforce）。
# 策略本体见 server/settings/csp.py（三层同源守护：本模块再导出 ↔ 页面层 nginx ↔ 隔离验证服务）。
from server.settings.csp import (  # noqa: E402,F401
    _CSP_DIRECTIVES,
    CONTENT_SECURITY_POLICY,
    CONTENT_SECURITY_POLICY_REPORT_ONLY,
)

# Prometheus 指标采集（默认关闭）：仅在显式启用时挂载，避免无谓开销与端点暴露。
# 开关与令牌**无条件导出**：读取方（common/api/metrics.py）走 getattr(settings, ...)，
# 漏导出会让端点永远 404（2026-09-16 实测踩中，与 SECURITY_AES_V1_DECRYPT_ENABLED 同类缺陷）
METRICS_ENABLED = CONFIG.METRICS_ENABLED
METRICS_TOKEN = CONFIG.METRICS_TOKEN
if METRICS_ENABLED:
    MIDDLEWARE.append("common.core.middleware.MetricsMiddleware")

# django-silk 性能剖析（性能基线）：config.yml 中 `SILK_ENABLED: true` 显式开启，
# 仅限 DEBUG/DEBUG_DEV 环境；剖析开销较大，k6 基线测定必须在关闭 silk 的状态下执行，
# silk 仅用于低并发下的单接口 SQL/profiling 剖析。开启后需 `python manage.py migrate` 创建 silk 表
if CONFIG.SILK_ENABLED:
    if not (DEBUG or DEBUG_DEV):
        raise ImproperlyConfigured("SILK_ENABLED 仅允许在 DEBUG/DEBUG_DEV 环境开启（性能剖析工具不可用于生产）")
    if "silk" not in INSTALLED_APPS:
        INSTALLED_APPS.append("silk")
        MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")
    SILKY_AUTHENTICATION = True  # /silk 面板要求登录
    SILKY_AUTHORISATION = True  # 且要求员工/超级管理员权限
    SILKY_MAX_RECORDED_REQUESTS = 10_000
    SILKY_MAX_RECORDED_REQUESTS_CHECK_PERCENT = 5  # 降低落库概率检查频率，减少剖析自身开销
    SILKY_PYTHON_PROFILER = True  # 请求级 Python profiling；压测排查时如干扰明显可关闭
    SILKY_PYTHON_PROFILER_RESULT_PATH = os.path.join(PROJECT_DIR, "tmp", "silk_profiles")
    SILKY_IGNORE_PATHS = ("/api/health", "/api/static", "^/media", "^/api/system/auth/captcha")

ROOT_URLCONF = "server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(PROJECT_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# WSGI_APPLICATION = 'server.wsgi.application'
ASGI_APPLICATION = "server.asgi.application"

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

# Redis 缓存与 Channels 通道层拆分至 cache_channel.py（文件行数门禁），经 star-import 并入本模块
from .cache_channel import *  # noqa: F401,F403

# 数据库连接配置拆分至 databases.py（文件行数门禁），经 star-import 并入本模块；
# _resolve_db_engine 为下划线名不经 star-export，显式再导出维持既有导入路径
# （tests/unit/common/test_db_check.py 直接 from server.settings.base import _resolve_db_engine）
from .databases import *  # noqa: F401,F403
from .databases import _resolve_db_engine  # noqa: F401

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/
# http://www.i18nguy.com/unicode/language-identifiers.html
# LANGUAGE_CODE = 'en-us'
LANGUAGE_CODE = CONFIG.LANGUAGE_CODE

TIME_ZONE = CONFIG.TIME_ZONE

USE_I18N = True

USE_TZ = True

AUTH_USER_MODEL = "identity.UserInfo"

# 会话存储与密码哈希（SESSION_ENGINE / PASSWORD_HASHERS）见 libs.py 的
# 「Django 认证/会话」段（与 DRF/JWT/CORS 同属框架级配置层）。

# 认证 backend 链：LDAP bind 在前。LdapBindBackend 内部按
# LDAP_AUTH_ENABLED / LDAP_AUTH_PRIORITY 动态让位 ModelBackend，
# 关闭/降级时行为与纯本地账密完全一致
AUTHENTICATION_BACKENDS = [
    "identity.ldap.auth.LdapBindBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "api/static/"
DATA_DIR = os.path.join(PROJECT_DIR, "data")
STATIC_ROOT = os.path.join(DATA_DIR, "static")

# STATICFILES_FINDERS = (
#     "django.contrib.staticfiles.finders.FileSystemFinder",
#     "django.contrib.staticfiles.finders.AppDirectoriesFinder"
# )
# 收集静态文件
# python manage.py collectstatic


# Media配置
MEDIA_URL = "media/"
MEDIA_ROOT = os.path.join(DATA_DIR, "upload")
# 受保护媒体的 nginx 内部重定向前缀（默认空 = 应用进程输出；生产建议 /_protected_media）
MEDIA_X_ACCEL_PREFIX = CONFIG.MEDIA_X_ACCEL_PREFIX
from server.settings.storage import STORAGES  # noqa: E402,F401 可插拔存储后端装配（本体见该模块）

# 审计日志冷归档目录：默认 DATA_DIR/log_archive；环境变量 LOG_ARCHIVE_DIR 可覆盖
# （如指向异地同步目录。注意不要直接放备份卷根目录——db_backup.sh 的 prune_local 会按
#  *.sql.gz / *.media.tar.gz / *.sha256 后缀清理存量文件，见 docs/ops/pitr.md「审计冷归档」）
LOG_ARCHIVE_DIR = os.environ.get("LOG_ARCHIVE_DIR") or os.path.join(DATA_DIR, "log_archive")
FILE_UPLOAD_SIZE = CONFIG.FILE_UPLOAD_SIZE
PICTURE_UPLOAD_SIZE = CONFIG.PICTURE_UPLOAD_SIZE
FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# I18N translation
LOCALE_PATHS = [
    os.path.join(PROJECT_DIR, "locale"),
]

CACHE_KEY_TEMPLATE = {
    "config_key": "config",
    "make_token_key": "make_token",
    "download_url_key": "download_url",
    "pending_state_key": "pending_state",
    "websocket_group_key": "websocket_group",
    "upload_part_info_key": "upload_part_info",
    "black_access_token_key": "black_access_token",
    "common_resource_ids_key": "common_resource_ids",
    "websocket_message_result_key": "websocket_message_result",
    "mfa_confirm_state_key": "mfa_confirm_state",
    "mfa_otp_bind_key": "mfa_otp_bind",
    "mfa_otp_used_key": "mfa_otp_used",
    "user_token_revoked_key": "user_token_revoked",
    "session_token_revoked_key": "session_token_revoked",
}

APPEND_SLASH = False

HTTP_BIND_HOST = CONFIG.HTTP_BIND_HOST
HTTP_LISTEN_PORT = CONFIG.HTTP_LISTEN_PORT
GUNICORN_MAX_WORKER = CONFIG.GUNICORN_MAX_WORKER
CELERY_WORKER_COUNT = CONFIG.CELERY_WORKER_COUNT
# heavy 队列 worker 配置（见 conf.py 说明）
CELERY_HEAVY_POOL = CONFIG.CELERY_HEAVY_POOL
CELERY_HEAVY_CONCURRENCY = CONFIG.CELERY_HEAVY_CONCURRENCY
# celery flower 任务监控配置
CELERY_FLOWER_PORT = CONFIG.CELERY_FLOWER_PORT
CELERY_FLOWER_HOST = CONFIG.CELERY_FLOWER_HOST
CELERY_FLOWER_AUTH = CONFIG.CELERY_FLOWER_AUTH

# 错误聚合（SENTRY_DSN 为空时零开销），在 settings 加载期尽早初始化
from ..monitoring import init_monitoring  # noqa: E402

init_monitoring()
