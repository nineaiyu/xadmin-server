#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB 连接池判活回调测试（2030-03 DB 重启演练修复）。

覆盖：健康连接必须执行真实查询（空查询检不出半开连接）、closed/查询失败判死。
"""


class _FakeConn:
    def __init__(self, closed=False, fail=False):
        self.closed = closed
        self._fail = fail
        self.executed = []

    def execute(self, sql):
        if self._fail:
            raise RuntimeError("the connection is closed")
        self.executed.append(sql)


class TestCheckDbConnection:
    def test_healthy_connection_passes_with_real_query(self):
        from common.db import check_db_connection

        conn = _FakeConn()
        assert check_db_connection(conn) is True
        # 必须是真实查询：psycopg_pool 默认的空查询检不出「半开连接」
        assert conn.executed == ["SELECT 1"]

    def test_closed_connection_rejected(self):
        from common.db import check_db_connection

        assert check_db_connection(_FakeConn(closed=True)) is False

    def test_broken_connection_rejected(self):
        from common.db import check_db_connection

        assert check_db_connection(_FakeConn(fail=True)) is False


class TestHalfOpenConnectionOptions:
    """半开连接快速失败参数（第十七轮·真丢包演练修复，2026-09-18）。

    丢包时已建立连接进入半开态（对端收不到包、本端不知情）：无 socket 级超时则读操作
    挂到 TCP 重传耗竭（Linux 默认 ~15 分钟），期间 worker 同步处理线程被逐个占死
    （实测 health 亦排队无响应）。守护连接参数：
    - tcp_user_timeout：未确认数据超时即强制断开（recv 快速失败 → 池淘汰重建）；
    - keepalives 三件套：空闲连接探活。
    """

    def test_postgres_options_include_half_open_guards(self):
        from server.settings.base import DB_ENGINE, DB_OPTIONS

        assert DB_ENGINE == "postgresql", "测试配置应与生产形态一致（见 tests/settings_test.py）"
        assert DB_OPTIONS["tcp_user_timeout"] == 30000
        assert DB_OPTIONS["keepalives"] == 1
        assert DB_OPTIONS["keepalives_idle"] == 30
        assert DB_OPTIONS["keepalives_interval"] == 10
        assert DB_OPTIONS["keepalives_count"] == 3

    def test_connect_timeout_still_present(self):
        """第五轮修复的 connect_timeout（建连阶段快速失败）不被回归。"""
        from server.settings.base import DB_OPTIONS

        assert DB_OPTIONS["connect_timeout"] == 3

    def test_pool_getconn_timeout_configured(self):
        """池取用等待上限收紧（默认 30s → 5s）：故障时请求快速失败而非排队半分钟
        （第十七轮复演实测：半开连接清理后，请求仍因池等待满 30s）。"""
        from server.settings.base import DB_OPTIONS

        assert DB_OPTIONS["pool"]["timeout"] == 5

    def test_pool_reconnect_timeout_configured(self):
        """失败连接重连间隔收紧（默认 300s → 10s）：DB 恢复后健康指示快速回正
        （第十七轮复演观察：删规则后 db 指示恢复慢且抖动）。"""
        from server.settings.base import DB_OPTIONS

        assert DB_OPTIONS["pool"]["reconnect_timeout"] == 10
