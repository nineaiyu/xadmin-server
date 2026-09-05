# -*- coding: utf-8 -*-
"""索引命中回归测试（T3.3）。

以 EXPLAIN QUERY PLAN 断言高频列表/清理查询命中索引，防止后续模型改动
无意间退化成全表扫描。索引清单与评审结论见 docs/architecture/indexes.md。
"""
import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def explain_plan(sql: str, params: list | None = None) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN QUERY PLAN {sql}", params or [])
        return " ".join(row[3] for row in cursor.fetchall())


class TestIndexUsage:
    def test_operation_log_default_ordering_uses_index(self):
        plan = explain_plan(
            "SELECT id FROM system_operationlog ORDER BY created_time DESC"
        )
        assert "idx_oplog_created" in plan, plan

    def test_operation_log_module_filter_uses_composite_index(self):
        plan = explain_plan(
            "SELECT id FROM system_operationlog WHERE module = %s",
            ["面板"],
        )
        assert "idx_oplog_module_created" in plan, plan

    def test_login_log_default_ordering_uses_index(self):
        plan = explain_plan(
            "SELECT id FROM system_userloginlog ORDER BY created_time DESC"
        )
        assert "idx_loginlog_created" in plan, plan

    def test_message_user_read_owner_unread_uses_composite_index(self):
        plan = explain_plan(
            "SELECT id FROM notifications_messageuserread "
            "WHERE owner_id = %s AND unread = %s",
            [1, True],
        )
        # sqlite: SEARCH ... USING COVERING INDEX（pg: Index Scan / Bitmap Index Scan）
        assert "SEARCH" in plan and "INDEX" in plan, plan
        assert "owner_id=?" in plan and "unread=?" in plan, plan

    def test_upload_file_cleanup_query_uses_composite_index(self):
        """每日清理任务（PERF-13）按 (is_tmp, created_time) 扫描。"""
        plan = explain_plan(
            "SELECT id FROM system_uploadfile "
            "WHERE is_tmp = %s AND created_time < %s",
            [True, "2026-01-01"],
        )
        assert "idx_uploadfile_tmp_created" in plan, plan

    def test_user_username_exact_lookup_uses_unique_index(self):
        plan = explain_plan(
            "SELECT id FROM system_userinfo WHERE username = %s",
            ["xadmin"],
        )
        assert "SEARCH" in plan and "username=?" in plan and "INDEX" in plan, plan
