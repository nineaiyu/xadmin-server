# -*- coding: utf-8 -*-
"""
单元测试专用配置（Django settings 模块）。

- 数据库使用 sqlite3 内存库，无需 MySQL/PostgreSQL
- 缓存使用 tests/cache_backend.FakeRedisCache（进程内实现 lock /
  delete_pattern，MagicCacheData 等依赖），无需 Redis
- Channels 使用内存 channel layer
- Celery 任务改为同步执行，无需 broker

注意：本模块不能放在 server/settings/ 包内——import 时父包 __init__.py
会先执行并触发 config.yml 强制加载。放在独立模块中，可在导入正式
settings 之前先注入内存配置绕过文件依赖。
"""
from server.conf import Config, ConfigManager

_test_config = Config()
_test_config["SECRET_KEY"] = "test-only-secret-key-0123456789abcdef"
_test_config["XADMIN_APPS"] = ["demo"]  # 启用 demo app，供 BaseModelSet 冒烟测试使用

ConfigManager.load_user_config = classmethod(
    lambda cls, root_path=None, config_class=None: _test_config
)

from server.settings import *  # noqa: F401,F403,E402

DEBUG = False
DEBUG_DEV = False
SECRET_KEY = "test-only-secret-key-0123456789abcdef"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "ATOMIC_REQUESTS": True,
    }
}

CACHES = {
    "default": {
        "BACKEND": "tests.cache_backend.FakeRedisCache",
        "LOCATION": "test-cache",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "tests.channel_layer.TestInMemoryChannelLayer",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# 测试产生的上传文件统一写到 tmp 目录，避免污染 data/
import os  # noqa: E402

MEDIA_ROOT = os.path.join(PROJECT_DIR, "tmp", "test_media")