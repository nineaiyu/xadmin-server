# -*- coding: utf-8 -*-
"""
E2E 专用配置（Playwright 驱动的真实后端）。

与 settings_test 同源（sqlite + 进程内 FakeRedis + 内存 channel layer +
eager celery），但差异点：

- sqlite 使用文件库（tmp/e2e.sqlite3），供 runserver 进程持续读写，
  可被种子脚本一键重置
- 登录关闭验证码与前端加密（E2E 以明文密码走真实登录链路）
- DEBUG=True + ALLOWED_HOSTS=*，仅限本机 E2E 使用，禁止用于任何真实部署

同样不能放在 server/settings/ 包内，避免父包 __init__ 强制加载 config.yml。
"""
import os

from server.conf import Config, ConfigManager

from tests import settings_test as _base

# 说明：对 _test_config 的注入必须发生在 server.settings 导入之前才生效，
# settings_test 先于本模块加载了 server.settings，因此 E2E 差异项统一放在
# 本模块尾部的显式覆盖区（见文件下方）。
_test_config = _base._test_config

ConfigManager.load_user_config = classmethod(
    lambda cls, root_path=None, config_class=None: _test_config
)

from server.settings import *  # noqa: F401,F403,E402

DEBUG = True
ALLOWED_HOSTS = ["*"]

# SECURITY_* 常量在 server.settings.custom 导入时即从 CONFIG 冻结（彼时 _test_config
# 尚未注入 E2E 差异），必须在此后显式覆盖才会生效：
# 登录关验证码/加密（E2E 明文密码走真实链路），放宽失败锁定阈值避免用例互相影响
SECURITY_LOGIN_CAPTCHA_ENABLED = False
SECURITY_LOGIN_ENCRYPTED_ENABLED = False
SECURITY_LOGIN_LIMIT_COUNT = 50
VERIFY_CODE_LIMIT = 1000
# IP 限流必须放宽：E2E 全套件共享 127.0.0.1，且登录锁定用例会连续失败 50 次，
# 默认阈值（50 次/30min）会把本机 IP 整体封禁，导致后续所有用例无法登录
SECURITY_LOGIN_IP_LIMIT_COUNT = 100000

# 放开登录限流：E2E 套件 20+ 用例共享 127.0.0.1 的 login 50/h 配额，
# 打满后 rules/login 全部 429，登录页会退化为「当前服务器不允许登录」
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),
        "login": "10000/h",
    },
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(str(_base.PROJECT_DIR), "tmp", "e2e.sqlite3"),
        "ATOMIC_REQUESTS": True,
    }
}

# 站点配置等系统设置缓存走 FakeRedis（进程内），跨请求一致
CACHES = _base.CACHES
CHANNEL_LAYERS = _base.CHANNEL_LAYERS

# memory broker 上 inspect.ping() 会无限阻塞，healthz 跳过 celery 探测
HEALTH_CHECK_SKIP_CELERY = True
