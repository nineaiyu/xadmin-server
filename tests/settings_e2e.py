# -*- coding: utf-8 -*-
"""
E2E 专用配置（Playwright 驱动的真实后端）。

真库化（全真容器化测试迁移方案 §四 阶段 4，2026-10-01）：DATABASES 指向真实
PostgreSQL（compose.test.yml 同一容器，库名按 shard 隔离），重置语义由
scripts/e2e_seed.py 承担（原 sqlite「删文件重开」→ PG「DROP DATABASE WITH (FORCE)
+ CREATE」）；sqlite WAL/busy_timeout/IMMEDIATE 专项配置随之作废。缓存仍为进程内
FakeRedis、channel 仍为内存层——E2E 追求确定性回放，站点设置缓存与 WS 推送语义
已由真环境门禁档（settings_real）覆盖。

与 pytest 档的差异点：

- DEBUG=True + ALLOWED_HOSTS=*，仅限本机 E2E 使用，禁止用于任何真实部署
- 登录关闭验证码与前端加密（E2E 以明文密码走真实登录链路）
- 公共面（PASSWORD_HASHERS / MEDIA_ROOT / 日志隔离 / eager celery）经
  tests/settings_base star-import 继承——旧档只 `import settings_test as _base`
  无 star-import，PASSWORD_HASHERS / MEDIA_ROOT 实际从未继承（生产哈希 + data/media
  泄漏面），本档一并修复

同样不能放在 server/settings/ 包内，避免父包 __init__ 强制加载 config.yml。
"""

import os

from server.conf import Config, ConfigManager

# 库名按 shard 隔离：并行跑批器（xadmin-client scripts/e2e-parallel.mjs）传
# E2E_DB_NAME=xadmin_e2e_shard<i>；兼容旧分片变量 E2E_DB_FILENAME（去 .sqlite3 后缀）
_e2e_db_name = os.environ.get("E2E_DB_NAME", "")
if not _e2e_db_name and os.environ.get("E2E_DB_FILENAME"):
    _e2e_db_name = os.environ["E2E_DB_FILENAME"].removesuffix(".sqlite3")
if not _e2e_db_name:
    _e2e_db_name = "xadmin_e2e"

_e2e_config = Config()
_e2e_config["SECRET_KEY"] = "test-only-secret-key-0123456789abcdef"
_e2e_config["XADMIN_APPS"] = ["demo"]  # 二开样板页场景（demo.Book）依赖
# 前置 Config 注入（settings_real/settings_pg 同款形态）：DATABASES 由 base settings
# 按 PG 分支整体构造（ATOMIC_REQUESTS=True、psycopg 连接参数与生产同构），本模块
# 不再自行拼 DATABASES——库名经 DB_DATABASE 注入，每 shard 独立库互不串扰
_e2e_config["DB_ENGINE"] = "postgresql"
_e2e_config["DB_HOST"] = os.environ.get("DB_HOST", "127.0.0.1")
_e2e_config["DB_PORT"] = int(os.environ.get("DB_PORT", "55433"))
_e2e_config["DB_DATABASE"] = _e2e_db_name
_e2e_config["DB_USER"] = os.environ.get("DB_USER", "server")
_e2e_config["DB_PASSWORD"] = os.environ.get("DB_PASSWORD", "pgtest")

ConfigManager.load_user_config = classmethod(lambda cls, root_path=None, config_class=None: _e2e_config)

from server.settings import *  # noqa: F401,F403,E402

# 公共测试基座（DEBUG/CELERY eager/PASSWORD_HASHERS/EMAIL_BACKEND/MEDIA_ROOT/日志隔离）
from tests.settings_base import *  # noqa: F401,F403,E402

DEBUG = True
ALLOWED_HOSTS = ["*"]

# 用例断言匹配中文文案（如锁定提示 /已被锁定/）。LocaleMiddleware 会按请求
# Accept-Language 协商语言，CI 的 API 请求上下文无中文头时回退英文，断言全部
# 落空（实测 lockout 用例死循环打登录接口）。E2E 环境去掉协商、固定中文。
LANGUAGE_CODE = "zh-hans"
MIDDLEWARE = [m for m in MIDDLEWARE if m != "django.middleware.locale.LocaleMiddleware"]  # noqa: F405

# SECURITY_* 常量在 server.settings.custom 导入时即从 CONFIG 冻结（彼时 _e2e_config
# 尚未注入 E2E 差异），必须在此后显式覆盖才会生效：
# 登录关验证码/加密（E2E 明文密码走真实链路），放宽失败锁定阈值避免用例互相影响
SECURITY_LOGIN_CAPTCHA_ENABLED = False
SECURITY_LOGIN_ENCRYPTED_ENABLED = False
SECURITY_LOGIN_LIMIT_COUNT = 50
VERIFY_CODE_LIMIT = 1000
# 忘记密码链路 E2E：关图片验证码（无法自动识别），验证码值本身仍走真实发送/校验，
# 错误验证码拒绝分支在浏览器内实测（成功分支由后端集成测试覆盖）。邮件渠道默认因
# 未配置 SMTP 关闭（EMAIL_ENABLED=False），E2E 强制开启配合 locmem 后端——验证码
# 投递留在服务端进程内，浏览器内不可读取
EMAIL_ENABLED = True
SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED = False
# IP 限流必须放宽：E2E 全套件共享 127.0.0.1，且登录锁定用例会连续失败 50 次，
# 默认阈值（50 次/30min）会把本机 IP 整体封禁，导致后续所有用例无法登录
SECURITY_LOGIN_IP_LIMIT_COUNT = 100000

# MFA 敏感操作二次验证固定关闭：用户删除等敏感 API 未确认时返回 412，前端拦截层
# 会唤起验证弹窗并挂起原请求，既有 CRUD 用例（删除用户）没有该交互会全部落空。
# 412 协议/验证链路由 tests/integration/test_mfa_api.py 30 例覆盖，E2E 不重复验证。
SECURITY_MFA_CONFIRM_ENABLED = False

# 敏感操作告警固定关闭：删除类用例会触发 WS 站内信实时弹窗，恰好盖在抽屉
# 操作按钮上造成点击「element is not stable」（回收站恢复用例实测命中）。
# 告警链路由 tests/unit/system/test_operation_log_enhance.py 覆盖，E2E 不重复验证
SENSITIVE_OPERATION_METHODS = []

# 字段级审计 diff：E2E 的「变更历史」用例需要 diff 断言（生产按需经 config.yml 开启）
AUDIT_DIFF_MODELS = ["system.UserInfo"]

# 邀请开户发信链路：E2E 使用内存后端（不真实投递，也满足「邮件渠道已配置」判定）
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

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
        # O8 专用限流档同步放开：登录握手链路（临时令牌 / 验证码）与开放平台
        # 端点的 E2E 用例同样高频匿名请求，收紧档会先于业务断言打满（429 雪崩）
        "temp_token": "100000/m",
        "verify_code": "100000/m",
        "open_client": "100000/m",
        "oauth_client": "100000/m",
    },
}

# 站点配置等系统设置缓存走 FakeRedis（进程内），跨请求一致；channel 走内存层。
# 原定义随 sqlite 档（tests/settings_test.py，已退役删除）收编至此——仅 E2E 消费
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

# eager celery 继承自 settings_base（真实 broker 只引入时序 flaky）；此处补齐
# memory broker 的两个探针豁免（原档注释保留）：

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
