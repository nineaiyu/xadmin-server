# -*- coding: utf-8 -*-
"""
性能基线压测专用配置（docs/ops/performance-baseline.md）。

与日常开发/生产数据完全隔离：数据库与 Redis 均指向一次性专用容器
（见 docker run 命令于 performance-baseline.md §三），绝不指向日常 config.yml
所配置的实例。禁止用于任何真实部署。

- 登录关闭验证码/加密/临时 token：k6 脚本以明文密码走真实登录链路
- 放开 login/anon/user 限流：压测流量本身就是要测的对象（01-login 脚本
  的 login_throttled 计数非零即本轮作废）
- SILK_ENABLED 固定 False：silk 有侵入开销，会污染基线数据（§一）
- celery 指向专用 Redis 独立 db（不 eager）：broker 可达则发布不阻塞、
  无重试噪音；无 worker 时导入导出经 Inspect 探针自动走直接执行分支

注意：本模块不能放在 server/settings/ 包内——父包 __init__ 会强制加载
config.yml。SECURITY_* 常量在 custom.py 导入时即从 CONFIG 冻结，因此
差异项统一放在本模块尾部（star-import 之后）显式覆盖。
"""

import os

from server.conf import Config, ConfigManager

_loadtest_config = Config()
_loadtest_config["SECRET_KEY"] = os.environ.get("LOADTEST_SECRET_KEY", "loadtest-only-not-for-production")
_loadtest_config["DEBUG"] = False
_loadtest_config["DEBUG_DEV"] = False
_loadtest_config["SILK_ENABLED"] = False
# 专用容器连接参数：仅监听 127.0.0.1 的发布端口
_loadtest_config["DB_ENGINE"] = "postgresql"
_loadtest_config["DB_HOST"] = os.environ.get("LOADTEST_DB_HOST", "127.0.0.1")
_loadtest_config["DB_PORT"] = int(os.environ.get("LOADTEST_DB_PORT", "55432"))
_loadtest_config["DB_DATABASE"] = os.environ.get("LOADTEST_DB_NAME", "xadmin_loadtest")
_loadtest_config["DB_USER"] = os.environ.get("LOADTEST_DB_USER", "server")
_loadtest_config["DB_PASSWORD"] = os.environ.get("LOADTEST_DB_PASSWORD", "loadtest")
_loadtest_config["REDIS_HOST"] = os.environ.get("LOADTEST_REDIS_HOST", "127.0.0.1")
_loadtest_config["REDIS_PORT"] = int(os.environ.get("LOADTEST_REDIS_PORT", "56379"))
_loadtest_config["REDIS_PASSWORD"] = os.environ.get("LOADTEST_REDIS_PASSWORD", "loadtest")
_loadtest_config["ALLOWED_HOSTS"] = ["127.0.0.1", "localhost"]
_loadtest_config["GUNICORN_MAX_WORKER"] = int(os.environ.get("LOADTEST_WORKERS", "4"))

ConfigManager.load_user_config = classmethod(lambda cls, root_path=None, config_class=None: _loadtest_config)

from server.settings import *  # noqa: F401,F403,E402

DEBUG = False
DEBUG_DEV = False

# ASGI 连接风暴（首测发现，重要）：Django 的 ASGIHandler 为每个请求创建独立
# 线程（ThreadSensitiveContext），线程随请求结束消亡，其 DB 连接随之丢弃——
# base.py 的 CONN_MAX_AGE=600 在此形态下无效，等效于每请求新建 PG 连接；
# 持续 ~600rps 时临时端口耗尽（macOS/Linux 容器均实测 EADDRNOTAVAIL）→ 13-27% 500。
# 【已根因修复（2026-09-07）】：psycopg3 + Django server 端连接池
# （OPTIONS.pool，默认开启），before/after 压测对比见 docs/ops/performance-baseline.md


# 登录三开关全关（performance-baseline.md §三）：否则脚本无法完成登录
SECURITY_LOGIN_CAPTCHA_ENABLED = False
SECURITY_LOGIN_ENCRYPTED_ENABLED = False
SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False

# 限流全放开：压测套件共享 127.0.0.1，login 50/h 与 anon/user 60m 都会
# 中途打满并污染数据（429/999 不属于接口真实性能）
REST_FRAMEWORK = {  # noqa: F405  # star-import 覆写
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),  # noqa: F405
        "anon": "1000000/m",
        "user": "1000000/m",
        "login": "1000000/h",
    },
}

# celery 走专用 Redis 独立 db 15：发布快、无重试噪音；无 worker 消费，
# 导入导出经 Inspect 探针自动落入直接执行分支（与 E2E 同款语义）
CELERY_BROKER_URL = (
    f"redis://:{_loadtest_config['REDIS_PASSWORD']}"
    f"@{_loadtest_config['REDIS_HOST']}:{_loadtest_config['REDIS_PORT']}/15"
)
