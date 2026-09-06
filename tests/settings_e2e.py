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

from server.conf import ConfigManager

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

# 用例断言匹配中文文案（如锁定提示 /已被锁定/）。LocaleMiddleware 会按请求
# Accept-Language 协商语言，CI 的 API 请求上下文无中文头时回退英文，断言全部
# 落空（实测 lockout 用例死循环打登录接口）。E2E 环境去掉协商、固定中文。
LANGUAGE_CODE = "zh-hans"
MIDDLEWARE = [m for m in MIDDLEWARE if m != "django.middleware.locale.LocaleMiddleware"]  # noqa: F405

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
#
# 关键：anon/user 必须一并放开。登录握手链路（临时 Token、登录配置、验证码配置）
# 全部为匿名请求且未覆写 throttle_classes，走 DEFAULT_THROTTLE_CLASSES 的
# AnonRateThrottle（默认 60/m）。锁定用例单次就要 50+ 轮「取临时 Token + 登录」，
# anon 会先于 login 打满并雪崩，表现为后续用例集体登录失败（429 而非 401）。
REST_FRAMEWORK = {  # noqa: F405  # star-import 覆写
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),  # noqa: F405
        "anon": "100000/m",
        "user": "100000/m",
        "login": "10000/h",
    },
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(str(_base.PROJECT_DIR), "tmp", "e2e.sqlite3"),
        "ATOMIC_REQUESTS": True,
        # daphne 并发处理请求 + ATOMIC_REQUESTS 下裸 sqlite 会互踩写锁
        # （实测单请求可拖到 5s+ 并抛 "database is locked"）：
        # IMMEDIATE 在事务开始即取写锁避免锁升级死锁，WAL + busy_timeout
        # 让并发写排队而非直接报错（Django 5.1+ sqlite OPTIONS）
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA busy_timeout=15000;"
                "PRAGMA synchronous=NORMAL;"
            ),
        },
    }
}

# 站点配置等系统设置缓存走 FakeRedis（进程内），跨请求一致
CACHES = _base.CACHES
CHANNEL_LAYERS = _base.CHANNEL_LAYERS

# eager celery 与 settings_test 对齐。注意：本模块不能只依赖 _base —— settings_e2e
# 与 settings_test 之间没有 star-import，CELERY_* 必须在此显式声明；否则
# apply_async 会按 celery 默认值连接 localhost:5672（rabbitmq），连接重试
# 在 thread-sensitive 同步线程里可达分钟级，实测拖死整个 daphne 进程。
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

# memory broker 上 inspect.ping() 会无限阻塞，healthz 跳过 celery 探测
HEALTH_CHECK_SKIP_CELERY = True

# celery control 命令（inspect.active/ping）在 memory broker 上无 worker 回包且
# drain_events 无超时兜底，会永久阻塞；而导入导出分发前会调用 inspect.active()
# 探测 worker。channels 的 thread-sensitive 线程池把全部同步视图串行在同一线程，
# 单个阻塞请求即拖死整个 daphne 进程（实测 token/登录等全部接口超时）。
# 探针返回「无 worker」→ 导入导出走直接执行分支（同步语义，与 task=false 一致）。
# 注意：不能返回假 worker 走 apply_async —— 站内信 publish 链路的 async_to_sync
# 会在事件循环被 sync_to_async 占用时死锁（实测）。CELERY_* 已配 eager + memory
# broker，apply_async 仅在直通分支兜底触发时才会被使用。
from celery.app.control import Inspect  # noqa: E402


def _e2e_no_workers(self, *args, **kwargs):
    return None


Inspect.active = _e2e_no_workers
Inspect.ping = _e2e_no_workers

