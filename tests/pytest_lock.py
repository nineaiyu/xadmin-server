# -*- coding: utf-8 -*-
"""跨进程串行化 pytest 会话（PostgreSQL 会话级 advisory lock）。

两轮 pytest 并发时，pytest-django 会在 ``django_db_setup`` 里 DROP/CREATE 同名
测试库，把另一轮正在用的连接成片 ``pg_terminate_backend`` 掉。现场表现是
「DB CPU 打满、Python 无 CPU、SQL 日志只有几行」——赢的一轮在建库/迁移/灌种子，
输的一轮 worker 全卡在已断开的连接上。

库名检查是 check-then-act：两个进程同时启动时都能查到 0；而且 xdist worker 的
测试库带 ``_gwN`` 后缀，基名库在运行期根本不存在，按基名查恒为 0。这里改用
PostgreSQL 会话级 advisory lock：

- 锁随连接生命周期自动释放，进程被杀也不会残留；
- 对 xdist / 非 xdist / 同时启动都成立；
- 拿不到锁直接 ``pytest.exit``，把「跑一半互踩」变成启动时的可见失败。

连接失败按 fail-open 处理（告警后继续）：锁是防并发的护栏，不是测试前提；
可达性已由 ``tests/conftest.py`` 的 socket 预检覆盖。
"""

from __future__ import annotations

import os
import warnings
from typing import Any

# 两段 int4 组成 advisory lock key（"XADM" / "PYTE"），避免依赖 hashtext 的实现细节。
LOCK_KEY = (0x5841444D, 0x50595445)
LOCK_KEY_SQL = "SELECT pg_try_advisory_lock(%s, %s)"
UNLOCK_KEY_SQL = "SELECT pg_advisory_unlock(%s, %s)"


def _connect(db: dict[str, Any]) -> Any:
    """连接测试 PG 的 ``postgres`` 库（advisory lock 不依赖测试库是否存在）。"""
    import psycopg

    return psycopg.connect(
        host=db["HOST"],
        port=db["PORT"],
        dbname="postgres",
        user=db["USER"],
        password=db["PASSWORD"],
        connect_timeout=5,
        autocommit=True,
    )


def acquire(config: Any) -> None:
    """获取会话级排他锁；拿不到就退出 pytest。"""
    import pytest
    from django.conf import settings as dj_settings

    if os.environ.get("XADMIN_ALLOW_CONCURRENT_PYTEST") == "1":
        return
    if getattr(config, "_xadmin_pytest_lock_conn", None) is not None:
        return
    db = dj_settings.DATABASES.get("default") or {}
    if "postgresql" not in db.get("ENGINE", ""):
        return

    try:
        conn = _connect(db)
        with conn.cursor() as cursor:
            cursor.execute(LOCK_KEY_SQL, list(LOCK_KEY))
            acquired = bool(cursor.fetchone()[0])
    except Exception as exc:  # noqa: BLE001 锁不可用不阻断测试，但要留下可见告警
        warnings.warn(
            f"pytest 并发锁不可用（{exc!r}）：本轮未串行化，若另一轮 pytest 正在运行，可能互相 DROP 测试库。",
            RuntimeWarning,
            stacklevel=2,
        )
        return

    if not acquired:
        conn.close()
        pytest.exit(
            "另一个 pytest 会话正在使用同一测试栈（PG advisory lock 未获取）。\n"
            "两轮 pytest 并发会在 django_db_setup 里互相 DROP/CREATE 测试库，"
            "表现为 DB CPU 打满、Python 无 CPU、SQL 日志只有几行。\n"
            "先确认上一轮已结束：ps aux | grep -E 'pytest|py.test'\n"
            "确需强制并发可设 XADMIN_ALLOW_CONCURRENT_PYTEST=1。",
            returncode=1,
        )

    config._xadmin_pytest_lock_conn = conn


def release(config: Any) -> None:
    """释放会话级锁并关闭连接（幂等）。"""
    conn = getattr(config, "_xadmin_pytest_lock_conn", None)
    if conn is None:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(UNLOCK_KEY_SQL, list(LOCK_KEY))
    except Exception:  # noqa: BLE001 连接可能已断，释放失败由会话结束兜底
        pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 连接可能已关闭
            pass
        config._xadmin_pytest_lock_conn = None
