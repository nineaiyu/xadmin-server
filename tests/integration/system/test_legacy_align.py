# -*- coding: utf-8 -*-
"""原地结构对齐的核心假设守护：改名保留数据与约束、补列回填、可重复执行。

这是立项方案第一批的验收点——用真实 4.2.5 结构样本库证明：
1. ``ALTER TABLE ... RENAME TO`` 保留行、主键/UUID、关联与外键约束（二开表不需要动的前提）；
2. 目标端新增列按「可空直接加 / NOT NULL 带字面默认值回填后收紧」两种策略落地；
3. 无法自动化的事项（可调用默认值、目标端已删除的旧列）必须**显式报告**而不是静默跳过；
4. 对齐可重复执行（第二次全部跳过，不产生副作用）。
"""

from system.utils.platform import legacy_align, legacy_schema


def _rows(dsn: str, sql: str) -> list:
    import psycopg

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


class TestAlignment:
    def test_apply_preserves_data_keys_and_constraints(self, legacy_sample_fresh):
        dsn, meta = legacy_sample_fresh
        before_users = _rows(dsn, "select id, username, dept_id from system_userinfo order by id")

        dry = legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=True)
        assert dry["planned"] > 0
        # dry-run 不落任何 DDL
        assert (
            _rows(dsn, "select count(*) from information_schema.tables where table_name = 'system_userinfo'")[0][0] == 1
        )

        result = legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=False)
        assert result["executed"] == result["planned"]
        assert result["after"]["renamed_left"] == 0
        assert result["after"]["blockers"] == []
        assert result["after"]["unmatched_old"] == []

        # 行、主键与 UUID 原样保留（对齐不动数据）
        after_users = _rows(dsn, "select id, username, dept_id from identity_userinfo order by id")
        assert after_users == before_users
        assert after_users[0][0] == meta["ids"]["user_a"]
        # 旧表名已不存在，新表名就位
        assert (
            _rows(dsn, "select count(*) from information_schema.tables where table_name = 'system_userinfo'")[0][0] == 0
        )
        # 关联数据（部门三层链 / 角色菜单 / 公告定向）随改名保留
        assert _rows(
            dsn,
            "select code from identity_deptinfo order by rank",
        ) == [("D100",), ("D110",), ("D111",)]
        assert _rows(dsn, "select count(*) from identity_userrole_menu")[0][0] == 1
        assert _rows(dsn, "select count(*) from notifications_messagecontent_notice_dept")[0][0] == 1
        # 外键约束跟着改名走：identity_userinfo → identity_deptinfo 仍在
        assert (
            _rows(
                dsn,
                "select count(*) from pg_constraint where conrelid = 'identity_userinfo'::regclass"
                " and confrelid = 'identity_deptinfo'::regclass",
            )[0][0]
            == 2
        )

    def test_new_columns_are_added_and_backfilled(self, legacy_sample_fresh):
        dsn, _meta = legacy_sample_fresh
        result = legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=False)
        added = {(step["table"], step["column"]) for step in result["steps"] if step["step"] == "add_column"}
        assert ("system_menu", "deleted_at") in added
        assert ("identity_userinfo", "must_change_password") in added
        # 可空列：加完即可空；NOT NULL 带字面默认值的列：回填后约束在、服务端默认已去掉
        catalog = legacy_schema.introspect_schema(dsn)
        assert catalog["system_menu"]["deleted_at"]["nullable"] is True
        assert catalog["identity_userinfo"]["must_change_password"]["nullable"] is False
        assert catalog["identity_userinfo"]["must_change_password"]["default"] is None
        assert _rows(dsn, "select count(*) from identity_userinfo where must_change_password is null")[0][0] == 0

    def test_review_items_are_reported_not_silently_skipped(self, legacy_sample_fresh):
        dsn, _meta = legacy_sample_fresh
        result = legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=False)
        skipped = " ".join(result["skipped"])
        # 可调用默认值（列表字段）需要人工回填策略
        assert "identity_userinfo.allowed_mfa_types" in skipped
        assert "identity_userrole.allowed_mfa_types" in skipped
        # 目标端已删除的旧列（旧库有 mode_type、新模型没有）必须在残留差异里可见
        remaining = {item["new"]: item for item in result["after"]["changes"]}
        assert "mode_type" in remaining["identity_deptinfo"]["columns_removed"]
        assert result["aligned"] is False  # 仍有需人工处置项：不能宣称完全对齐

    def test_alignment_is_repeatable(self, legacy_sample_fresh):
        dsn, _meta = legacy_sample_fresh
        legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=False)
        second = legacy_align.apply_alignment(dsn, tag="v4.2.5", dry_run=False)
        assert second["executed"] == 0
        assert second["planned"] == 0
        assert second["steps"] == []
