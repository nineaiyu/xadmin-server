# -*- coding: utf-8 -*-
"""完整原地升级（结构 + 引用层 + 账本）的端到端守护。

在真实 4.2.5 结构样本库上跑 ``legacy_align.align_full``，断言四件事：
1. 结构与当前版本一致（改名 0 残留、缺失表 0、列差异只剩人工复核项）；
2. 数据原样（主键 / UUID / 关联 / 行数不变，外键随改名保留）；
3. 引用层重指（``django_content_type`` 无未命中与遗留旧域；字段树与数据权限里的模型标签已改写、无残留）；
4. 迁移账本对齐（本仓库 app 的记录 == 当前链；三方 app 记录保持不动，交由常规 migrate 增量处理）。

另外核对「新建表的结构与全新安装一致」（索引集合逐一对比），确保补表路径用的就是 Django 的建表语义。
"""

import json

import psycopg
import pytest
from django.conf import settings
from django.db.migrations.loader import MigrationLoader

from ops.legacy import align as legacy_align
from ops.legacy import schema as legacy_schema

# 对齐器需要 Django 连接能力（schema editor / migration loader）连**样本库**：
# 用 django_db_blocker 临时解除访问拦截即可（不用 django_db 标记——那会把样本库别名拉进
# 测试装置的库白名单与测试库重定向，反而连错库；样本库与被测库互不影响）。


def _rows(dsn: str, sql: str) -> list:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def _index_names(dsn: str, table: str) -> set[str]:
    return {row[0] for row in _rows(dsn, f"select indexname from pg_indexes where tablename = '{table}'")}


def _project_ledger(dsn: str) -> set[tuple[str, str]]:
    loader = MigrationLoader(None, ignore_no_migrations=False)
    apps = {
        app
        for (app, _name), migration in loader.disk_migrations.items()
        if legacy_align.is_project_migration(migration)
    }
    rows = _rows(dsn, "select app, name from django_migrations")
    return {(app, name) for app, name in rows if app in apps}


def _expected_project_ledger() -> set[tuple[str, str]]:
    loader = MigrationLoader(None, ignore_no_migrations=False)
    return {
        (app, name)
        for (app, name), migration in loader.disk_migrations.items()
        if legacy_align.is_project_migration(migration)
    }


@pytest.fixture(autouse=True)
def _allow_legacy_db(django_db_blocker):
    """整个用例（含夹具与用例体）都允许访问样本库。"""
    with django_db_blocker.unblock():
        yield


@pytest.fixture
def aligned_sample(legacy_sample_fresh):
    """对齐完成后的样本库（含 tag）。"""
    dsn, meta = legacy_sample_fresh
    result = legacy_align.align_full(dsn, tag=meta["tag"], dry_run=False)
    return dsn, meta, result


class TestFullAlignment:
    def test_structure_is_complete(self, aligned_sample):
        dsn, _meta, result = aligned_sample
        assert result["dry_run"] is False
        assert result["after"]["renamed_left"] == 0
        assert result["after"]["blockers"] == []
        assert result["after"]["unmatched_old"] == []
        assert result["after"]["missing_tables"] == []
        assert result["aligned"] is True
        # 复检里剩下的都是「人工复核项」：可调用默认值的 NOT NULL 列、旧列多余、列类型变更
        for change in result["after"]["changes"]:
            assert change["columns_added"] or change["columns_type_changed"] or change["columns_removed"]

    def test_data_and_relations_are_preserved(self, aligned_sample):
        dsn, meta, _result = aligned_sample
        users = _rows(dsn, "select id, username, dept_id from identity_userinfo order by id")
        assert users[0][0] == meta["ids"]["user_a"]
        assert users[0][2] is not None  # 部门外键仍指向原 UUID
        assert _rows(dsn, "select code from identity_deptinfo order by rank") == [("D100",), ("D110",), ("D111",)]
        assert _rows(dsn, "select count(*) from identity_userrole_menu")[0][0] == 1
        assert _rows(dsn, "select count(*) from notifications_messagecontent_notice_dept")[0][0] == 1
        assert _rows(dsn, "select count(*) from notifications_messageuserread")[0][0] == 2
        # 外键约束随改名保留（二开表不需要动的前提）
        assert (
            _rows(
                dsn,
                "select count(*) from pg_constraint where conrelid = 'identity_userinfo'::regclass"
                " and confrelid = 'identity_deptinfo'::regclass",
            )[0][0]
            == 2
        )

    def test_content_types_are_rebound(self, aligned_sample):
        _dsn, _meta, result = aligned_sample
        content_types = result["content_types"]
        assert content_types["missing"] == []
        assert content_types["stale"] == []
        assert content_types["updated"] == len(content_types["moves"]) == 7
        # 迁移后的标签落在新域上（权限点通过 content_type_id 关联，因此同步生效）
        rows = _rows(_dsn, "select app_label, model from django_content_type")
        labels = {(app, model) for app, model in rows}
        assert ("identity", "userinfo") in labels
        assert ("file", "uploadfile") in labels
        assert ("system", "monitor") in labels
        assert ("system", "userinfo") not in labels

    def test_stored_references_are_rewritten(self, aligned_sample):
        dsn, _meta, result = aligned_sample
        targets = {item["target"]: item["changed"] for item in result["references"]["targets"]}
        assert targets["system_modellabelfield.name"] == 2  # 模型节点与字段节点各一行
        assert targets["system_datapermission.rules"] == 1
        assert result["references"]["leftovers"] == []
        names = [row[0] for row in _rows(dsn, "select name from system_modellabelfield order by name")]
        assert names == ["identity.userinfo", "identity.userinfo.username"]
        rules = _rows(dsn, "select rules from system_datapermission")
        assert "identity.deptinfo" in str(rules[0][0]) and "system.deptinfo" not in str(rules[0][0])

    def test_project_ledger_matches_current_chain(self, aligned_sample):
        dsn, _meta, _result = aligned_sample
        assert _project_ledger(dsn) == _expected_project_ledger()
        # 三方 app 的记录保持原样（它们的增量由常规 migrate 处理）
        third_party = {(app, name) for app, name in _rows(dsn, "select app, name from django_migrations")}
        assert ("django_celery_results", "0001_initial") in third_party

    def test_second_run_is_noop(self, aligned_sample):
        dsn, meta, _result = aligned_sample
        again = legacy_align.align_full(dsn, tag=meta["tag"], dry_run=False)
        assert again["structure"]["planned"] == 0
        assert again["missing_tables"] == []
        assert again["missing_fields"] == []
        assert again["missing_indexes"] == []
        assert again["missing_constraints"] == []
        assert again["ledger"]["deleted"] == 0
        assert again["ledger"]["recorded"] == 0
        assert all(item["changed"] == 0 for item in again["references"]["targets"])
        assert again["content_types"]["updated"] == 0


@pytest.mark.parametrize("tag", ["v4.1.3", "v4.2.3"])
def test_older_version_families_align_in_place(tag, sample_factory):
    """更早的版本家族（4.1.x / 4.2.x）同样能原地对齐：结构完整 + 账本一致 + 数据原样。"""
    if not legacy_schema.load_tag_models(tag):
        pytest.skip(f"仓库内没有 {tag} 的迁移历史（单仓检出）")
    dsn, _meta = sample_factory(tag)
    before = _rows(dsn, "select id, username from system_userinfo order by id")
    result = legacy_align.align_full(dsn, tag=tag, dry_run=False)
    assert result["after"]["renamed_left"] == 0
    assert result["after"]["blockers"] == []
    assert result["after"]["unmatched_old"] == []
    assert result["after"]["missing_tables"] == []
    assert result["aligned"] is True
    assert _project_ledger(dsn) == _expected_project_ledger()
    assert _rows(dsn, "select id, username from identity_userinfo order by id") == before
    assert result["content_types"]["missing"] == []
    assert result["references"]["leftovers"] == []


class TestConnectionSwitching:
    """一个进程连续处理多个库：连接必须跟着目标库切换（否则 DDL 会打到上一个库）。"""

    def test_register_connection_switches_targets(self, legacy_sample_fresh):
        from ops.legacy import structure as legacy_structure

        dsn, _meta = legacy_sample_fresh
        first = legacy_structure.connect_legacy(dsn)
        with first.cursor() as cursor:
            cursor.execute("select current_database()")
            assert cursor.fetchone()[0] == dsn.rsplit("/", 1)[-1]
        second = legacy_structure.connect_legacy("postgresql://user:pwd@127.0.0.1:55433/another_db")
        assert second.settings_dict["NAME"] == "another_db"
        third = legacy_structure.connect_legacy(dsn)
        with third.cursor() as cursor:
            cursor.execute("select current_database()")
            assert cursor.fetchone()[0] == dsn.rsplit("/", 1)[-1]
        legacy_structure.close_legacy(third)


class TestUpgradeCheckIntegration:
    """体检命令与原地升级引擎的联动：切分前的样本库必须被判为「可原地升级」。"""

    def test_pre_split_sample_is_reported_as_inplace_capable(self, legacy_sample_fresh):
        from io import StringIO

        from django.core.management import call_command

        dsn, meta = legacy_sample_fresh
        stdout = StringIO()
        call_command("upgrade_check", "--dsn", dsn, "--tag", meta["tag"], "--json", stdout=stdout)
        payload = json.loads(stdout.getvalue())
        assert payload["verdict"] == "legacy-inplace-capable"
        assert payload["exit_code"] == 0
        inplace = payload["inplace"]
        assert inplace["capable"] is True
        assert inplace["renames"] >= 14
        assert inplace["blockers"] == []
        # 二开表会被列出来（原样保留），不进阻断项
        assert isinstance(inplace["third_party_tables"], list)


class TestStructureParityWithFreshInstall:
    """补表路径必须与全新安装一致：新建表的索引集合逐一对齐（结构出自 Django 的 CreateModel）。"""

    def test_created_tables_match_fresh_database(self, aligned_sample):
        dsn, _meta, result = aligned_sample
        fresh = settings.DATABASES["default"]
        fresh_dsn = f"postgresql://{fresh['USER']}:{fresh['PASSWORD']}@{fresh['HOST']}:{fresh['PORT']}/{fresh['NAME']}"
        created = [table for table in result["missing_tables"] if table.startswith(("approval_", "ai_", "dataset_"))]
        assert created, "样本对齐后应当新建业务域表"
        for table in created:
            assert _index_names(dsn, table) == _index_names(fresh_dsn, table), table
