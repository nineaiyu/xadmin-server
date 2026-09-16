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
