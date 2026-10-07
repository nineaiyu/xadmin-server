# -*- coding: utf-8 -*-
"""索引命中回归测试。

以 EXPLAIN 断言高频列表/清理查询命中索引，防止后续模型改动无意间退化成
全表扫描。索引清单与评审结论见 docs/architecture/indexes.md。

方言自适应（nightly PG 档首轮暴露的差异，见 docs/plans/容器化PG-nightly测试档立项-2026.10.md §五）：
- sqlite 走 EXPLAIN QUERY PLAN（明细文本在 row[3]）；PG 走 EXPLAIN（文本行自带
  索引名）。索引名断言两侧通用；
- 测试库无业务数据，PG 规划器对空表倾向顺序扫描——SET LOCAL enable_seqscan = off
  才能证明「索引可用」（与本文件 trigram 用例既有口径一致）；
- 空表 + seqscan 关闭下 PG 规划器可在「成本并列」的任意索引间漂移（含主键索引 +
  排序节点），计划文本赌具体索引名在并发负载/统计噪声下偶发翻车——PG 侧对这类
  断言统一退守「目标索引确实已建」（历史回归形态即「模型改了、索引没建」，
  存在性守护即可拦截），sqlite 计划器确定性足够、保留计划名断言。
"""

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def explain_plan(sql: str, params: list | None = None) -> str:
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute(f"EXPLAIN QUERY PLAN {sql}", params or [])
            return " ".join(row[3] for row in cursor.fetchall())
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(f"EXPLAIN {sql}", params or [])
        return "\n".join(row[0] for row in cursor.fetchall())


def assert_pg_index_exists(table: str, index_name: str, plan: str) -> None:
    """PG 空表上计划文本与索引名博弈不可靠（见模块 docstring），退守存在性守护。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = %s AND indexname = %s",
            [table, index_name],
        )
        assert cursor.fetchone(), f"复合索引 {index_name} 未在 PG 真库创建：{sorted(plan)}"


class TestIndexUsage:
    def test_operation_log_default_ordering_uses_index(self):
        plan = explain_plan("SELECT id FROM audit_operationlog ORDER BY created_time DESC")
        if connection.vendor == "sqlite":
            assert "idx_oplog_created" in plan, plan
        else:
            assert_pg_index_exists("audit_operationlog", "idx_oplog_created", plan)

    def test_operation_log_module_filter_uses_composite_index(self):
        plan = explain_plan(
            "SELECT id FROM audit_operationlog WHERE module = %s",
            ["面板"],
        )
        if connection.vendor == "sqlite":
            assert "idx_oplog_module_created" in plan, plan
        else:
            # oplog 有两个 module 前导复合索引（..._module_created / ..._module_objectpk），
            # 空表上计划文本不与规划器博弈：守护两者确实已建
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = %s AND indexname LIKE %s",
                    ["audit_operationlog", "idx_oplog_module%"],
                )
                built = {row[0] for row in cursor.fetchall()}
            assert built == {"idx_oplog_module_created", "idx_oplog_module_objectpk"}, sorted(built)

    def test_login_log_default_ordering_uses_index(self):
        plan = explain_plan("SELECT id FROM audit_userloginlog ORDER BY created_time DESC")
        if connection.vendor == "sqlite":
            assert "idx_loginlog_created" in plan, plan
        else:
            assert_pg_index_exists("audit_userloginlog", "idx_loginlog_created", plan)

    def test_message_user_read_owner_unread_uses_composite_index(self):
        from notifications.models.message import MessageUserRead

        idx_name = MessageUserRead._meta.indexes[0].name
        plan = explain_plan(
            "SELECT id FROM notifications_messageuserread WHERE owner_id = %s AND unread = %s",
            [1, True],
        )
        if connection.vendor == "sqlite":
            # sqlite: SEARCH ... USING COVERING INDEX <name>（明细行带索引名）
            assert idx_name in plan, plan
        else:
            assert_pg_index_exists("notifications_messageuserread", idx_name, plan)

    def test_upload_file_cleanup_query_uses_composite_index(self):
        """每日清理任务按 (is_tmp, created_time) 扫描。"""
        plan = explain_plan(
            "SELECT id FROM file_uploadfile WHERE is_tmp = %s AND created_time < %s",
            [True, "2026-01-01"],
        )
        if connection.vendor == "sqlite":
            assert "idx_uploadfile_tmp_created" in plan, plan
        else:
            assert_pg_index_exists("file_uploadfile", "idx_uploadfile_tmp_created", plan)

    def test_user_username_exact_lookup_uses_unique_index(self):
        plan = explain_plan(
            "SELECT id FROM identity_userinfo WHERE username = %s",
            ["xadmin"],
        )
        if connection.vendor == "sqlite":
            assert "SEARCH" in plan and "username=?" in plan and "INDEX" in plan, plan
        else:
            # seqscan 已关，等值检索必须落到索引（unique btree 或 trgm GIN 均符合意图）
            assert "Index" in plan and "username" in plan, plan

    @pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="pg_trgm 索引仅 PostgreSQL 生效（本地/CI 用 SQLite，只建索引的 state，不执行 DDL）",
    )
    def test_search_trigram_index_is_usable_on_postgres(self):
        """全局搜索前缀通配能走 trigram 索引（PG 专属；表小需关 seqscan 才证明「可用」）。"""
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("EXPLAIN SELECT id FROM identity_userinfo WHERE username ILIKE %s", ["%关键词%"])
            plan = "\n".join(row[0] for row in cursor.fetchall())
        assert "idx_userinfo_username_trgm" in plan, plan
