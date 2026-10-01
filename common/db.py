#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据库连接池健康检查（psycopg_pool 的 check 回调）。

背景（2030-03 季度演练·DB 重启场景，2026-09-16）：psycopg_pool 默认的
``ConnectionPool.check_connection`` 以空查询（no-op）判活，无法发现「PG 重启后
服务端已断开、但客户端尚未读到终止报文」的半开连接——实测坏连接被反复取出，
health 与业务请求持续失败（"the connection is closed"），直到进程重启才恢复。
本回调执行真实 ``SELECT 1``：坏连接在「取用」阶段即被识别并淘汰（池自动补新连接）。

装载：``server/settings/base.py`` 的 ``DB_OPTIONS["pool"]["check"]``（池模式）。
"""

from psycopg.pq import TransactionStatus


def check_db_connection(conn) -> bool:
    """连接存活校验：closed 或真实查询失败返回 False（池将淘汰并重建）。

    探针必须自清事务：池归还连接时只回滚事务、不恢复 psycopg 级 autocommit；
    若连接带着 autocommit=False 回池（如事务内被 close_old_connections 关闭），
    本探针的 SELECT 1 会在取用时开启新事务（INTRANS），下一个取用者随后的
    set_autocommit 直接炸并循环污染池（2026-10-01 nightly PG 档首轮暴露，
    处置见 docs/plans/容器化PG-nightly测试档立项-2026.10.md §五）。
    """
    if getattr(conn, "closed", False):
        return False
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 判活失败即视为不可用，交由池淘汰
        return False
    finally:
        pgconn = getattr(conn, "pgconn", None)  # 伪连接（测试替身）无 pgconn，跳过
        if pgconn is not None and pgconn.transaction_status == TransactionStatus.INTRANS:
            conn.rollback()
