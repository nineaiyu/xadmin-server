# -*- coding: utf-8 -*-
"""
PG nightly 测试专用配置（容器化 PG 的 nightly 测试档，docs/plans/容器化PG-nightly测试档立项-2026.10.md）。

单变量换库（G2）：与 tests/settings_real（真环境门禁档）唯一的差异是不接真 Redis
——CACHES 仍为进程内 FakeRedis、channel 仍为内存层（本档自有定义，基座有意不含
三件套分支），DATABASES 指向真实 PostgreSQL（与生产/loadtest 同 17 系大版本），
作为真环境门禁的回退兜底档（周调度，全真容器化测试迁移方案 §四 回退方案）。
方言盲区已由门禁档收口（2026-10-01 起该档从「nightly 主动发现」转为「兜底」角色）。

xdist 每 worker 独立库（禁止共享库）：DB_DATABASE 按 PYTEST_XDIST_WORKER 加后缀，
settings 按 worker 进程各导入一次、pytest-django 自动建 test_<库名> 并跑迁移，天然
隔离。多进程共享同一文件库/真库的历史 flaky 见 docs/metrics.md 2026-09-08 行
（2026-09-08 教训）。

注意：本模块不能放在 server/settings/ 包内——父包 __init__ 会强制加载 config.yml。
连接参数默认对齐 compose.test.yml（端口 55433 与 loadtest 的 55432 错开，
两者容器可共存）；CI 由 workflow 顶层 env 注入同名变量。
"""

import os

from server.conf import Config, ConfigManager

_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
_pg_database = os.environ.get("DB_DATABASE", "xadmin_pgtest")
if _worker:
    _pg_database = f"{_pg_database}_{_worker}"

_pg_config = Config()
_pg_config["SECRET_KEY"] = "test-only-secret-key-0123456789abcdef"
_pg_config["XADMIN_APPS"] = ["demo"]  # 与测试基座对齐：启用 demo app
# 前置 Config 注入（loadtest/settings_loadtest.py 同款形态）：让 base settings 的
# DATABASES 按真实 PG 参数构造——DB_POOL 默认开启，psycopg3 连接池（OPTIONS.pool）
# 由此被测试首次真实创建（立项文档 §一.3 的被测面）。池参数（min/max_size 等）
# 用 defaults.py 缺省值即可。
_pg_config["DB_ENGINE"] = "postgresql"
_pg_config["DB_HOST"] = os.environ.get("DB_HOST", "127.0.0.1")
_pg_config["DB_PORT"] = int(os.environ.get("DB_PORT", "55433"))
_pg_config["DB_DATABASE"] = _pg_database
_pg_config["DB_USER"] = os.environ.get("DB_USER", "server")
_pg_config["DB_PASSWORD"] = os.environ.get("DB_PASSWORD", "pgtest")

ConfigManager.load_user_config = classmethod(lambda cls, root_path=None, config_class=None: _pg_config)

from server.settings import *  # noqa: F401,F403,E402

# 公共测试基座（无数据库/缓存/通道分支，不会覆写上方构造的真库配置）
from tests.settings_base import *  # noqa: F401,F403,E402

# FakeRedis / 内存 channel 层：nightly 档只验证 PG 方言面（与 settings_e2e 同款定义，
# 原收编自 sqlite 档 settings_test）；真 Redis 语义面由门禁档 settings_real 覆盖
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
