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


def check_db_connection(conn) -> bool:
    """连接存活校验：closed 或真实查询失败返回 False（池将淘汰并重建）。"""
    if getattr(conn, "closed", False):
        return False
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 判活失败即视为不可用，交由池淘汰
        return False
