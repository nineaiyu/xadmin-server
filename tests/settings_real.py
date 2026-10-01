# -*- coding: utf-8 -*-
"""
真环境测试专用配置（全真容器化测试迁移方案，docs/plans/全真容器化测试迁移方案-2026.10.md §3.1）。

真实 PostgreSQL 17 + Redis 8 容器全面替换 sqlite/FakeRedis：DATABASES / CACHES /
CHANNEL_LAYERS 全部指向真实后端（生产同款 backend 与参数），celery 保持 eager +
memory://（方案 §3.4 论证：真实 broker 不改变任务体正确性，只引入时序 flaky）。
PASSWORD_HASHERS / MEDIA_ROOT / EMAIL / 日志隔离等经 tests/settings_base 继承，
不重写（单变量原则）。

本档自 2026-10-01 起为 pytest.ini 缺省（方案 §四 阶段 3：CI 门禁真环境化，
sqlite 档退役删除）；依赖由 compose.test.yml 或 CI services 提供，不可达时
tests/conftest.py 预检 fail-fast 并给出恢复指引。

环境变量驱动（CI services 与本地 compose.test.yml 容器同构）：
- DB_HOST/DB_PORT/DB_DATABASE/DB_USER/DB_PASSWORD —— PostgreSQL（settings_pg 已验证形态）
- REDIS_HOST/REDIS_PORT/REDIS_PASSWORD            —— Redis（新增）
- PYTEST_XDIST_WORKER —— xdist 每 worker 独立库 + Redis 隔离参数（见下）

Redis 的 xdist 隔离（方案 §3.2，本迁移核心风险点）：FakeRedis 是进程内的天然隔离，
真 Redis 被全部 worker 共享，采用「db 编号 + 键前缀」双保险：
- db 编号：Redis 16 个逻辑库，db0 兜底保留，worker gwN → DEFAULT_CACHE_ID = 1 + N%15。
  get_redis_connection（ReentrantLock / metrics / CacheList 等裸连接消费面）与 django
  cache 同池同库，一并隔离；
- 键前缀：django cache 全部键（含限流计数器、magic 缓存）经 test_redis_key_func 加
  "tN:" 前缀。生产的 redis_key_func 是恒等实现（KEY_PREFIX 不落键），因此测试前缀必须
  走 KEY_FUNCTION 覆盖而非 KEY_PREFIX；delete_pattern / keys 经 make_pattern /
  reverse_key 同源拼前缀，SCAN 命中范围与逻辑键回读天然自洽。db 循环复用（>15 worker
  时两 worker 共库）由前缀兜底，conftest._clean_cache 只按前缀清理、永不 flushdb；
- channel layer：共享 CHANNEL_LAYERS_CACHE_ID 逻辑库，按 worker 加 asgi 前缀隔离
  （channels_redis prefix 参数）；在线用户索引键 online_users_key 同源于 prefix，
  无需额外处理。

注意：本模块不能放在 server/settings/ 包内——父包 __init__ 会强制加载 config.yml。
端口缺省对齐 compose.test.yml（PG 55433 与 nightly/loadtest 的 55432 错开可共存；
Redis 56379 与 loadtest 同端口，两者不可同时运行）；CI 由 workflow 顶层 env 注入同名变量。
"""

import os

from server.conf import Config, ConfigManager

_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
_worker_num = int(_worker[2:]) if _worker.startswith("gw") else 0
# conftest._clean_cache 依赖本前缀收敛清理范围（getattr 缺省 None → 无该属性即非真环境档）
TEST_REDIS_KEY_PREFIX = f"t{_worker_num}"

_pg_database = os.environ.get("DB_DATABASE", "xadmin_realtest")
if _worker:
    _pg_database = f"{_pg_database}_{_worker}"

_real_config = Config()
_real_config["SECRET_KEY"] = "test-only-secret-key-0123456789abcdef"
_real_config["XADMIN_APPS"] = ["demo"]  # 与测试基座对齐：启用 demo app
# 前置 Config 注入（loadtest/settings_loadtest.py 同款形态）：让 base settings 的
# DATABASES / CACHES / CHANNEL_LAYERS 按真实 PG + Redis 参数构造——psycopg3 连接池
# （OPTIONS.pool）与 django_redis（生产同款 backend 与参数）由此被测试首次真实创建。
_real_config["DB_ENGINE"] = "postgresql"
_real_config["DB_HOST"] = os.environ.get("DB_HOST", "127.0.0.1")
_real_config["DB_PORT"] = int(os.environ.get("DB_PORT", "55433"))
_real_config["DB_DATABASE"] = _pg_database
_real_config["DB_USER"] = os.environ.get("DB_USER", "server")
_real_config["DB_PASSWORD"] = os.environ.get("DB_PASSWORD", "pgtest")
_real_config["REDIS_HOST"] = os.environ.get("REDIS_HOST", "127.0.0.1")
_real_config["REDIS_PORT"] = int(os.environ.get("REDIS_PORT", "56379"))
_real_config["REDIS_PASSWORD"] = os.environ.get("REDIS_PASSWORD", "")
# worker 独立逻辑库：db0 兜底保留，gwN → 1 + N%15（Redis 共 16 库，循环复用由前缀兜底）
_real_config["DEFAULT_CACHE_ID"] = 1 + (_worker_num % 15)

ConfigManager.load_user_config = classmethod(lambda cls, root_path=None, config_class=None: _real_config)

from server.settings import *  # noqa: F401,F403,E402

# 公共测试基座（无数据库/缓存/通道分支，不会覆写上方构造的真环境配置）
from tests.settings_base import *  # noqa: F401,F403,E402

# 键前缀双保险（见模块 docstring）
CACHES["default"]["KEY_FUNCTION"] = "tests.settings_real.test_redis_key_func"  # noqa: F405
CACHES["default"]["REVERSE_KEY_FUNCTION"] = "tests.settings_real.test_redis_reverse_key_func"  # noqa: F405

# channel layer 按 worker 前缀隔离：prefix 是 channels_redis 的 layer 层参数
# （RedisChannelLayer 构造参数，默认 asgi），必须放 CONFIG 顶层而非 hosts[0]——
# hosts 内的键会透传给 redis-py 连接池，多一个 prefix 直接 TypeError
CHANNEL_LAYERS["default"]["CONFIG"]["prefix"] = f"asgi{_worker_num}"  # noqa: F405


def test_redis_key_func(key, key_prefix, version):
    """worker 前缀版 KEY_FUNCTION（生产 redis_key_func 为恒等实现，前缀不落键）。"""
    return f"{TEST_REDIS_KEY_PREFIX}:{key}"


def test_redis_reverse_key_func(key: str) -> str:
    """test_redis_key_func 的逆映射：cache.keys() 返回逻辑键（与生产口径一致）。"""
    prefix = f"{TEST_REDIS_KEY_PREFIX}:"
    return key[len(prefix) :] if key.startswith(prefix) else key
