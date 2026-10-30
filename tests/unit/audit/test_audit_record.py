# -*- coding: utf-8 -*-
"""审计写入统一入口 record()：序列化 / 截断 / creator 解析 / 异常兜底。"""

import pytest

from audit.services import OperationLog, record

pytestmark = pytest.mark.django_db


class TestAuditRecord:
    def test_writes_row_with_changes_serialized(self, superuser):
        record(
            module="test:audit",
            object_pk=42,
            path="/api/test/",
            method="POST",
            status_code=1000,
            response_code=200,
            changes={"action": "rotate", "ok": True},
            creator=superuser,
        )
        row = OperationLog.objects.get(module="test:audit")
        assert row.object_pk == "42"
        assert row.path == "/api/test/"
        assert row.method == "POST"
        assert row.status_code == 1000
        assert row.response_code == 200
        assert '"action": "rotate"' in row.changes
        assert row.creator == superuser

    def test_changes_truncated_to_limit(self):
        from audit.services import CHANGES_MAX_LENGTH

        record(module="test:audit", changes={"blob": "x" * (CHANGES_MAX_LENGTH + 100)})
        row = OperationLog.objects.get(module="test:audit")
        assert len(row.changes) == CHANGES_MAX_LENGTH

    def test_non_str_fields_truncated_by_model_limits(self):
        """module/object_pk/path/method 超长按模型 max_length 截断（PG 对 varchar 超长直接报错）。"""
        record(module="m" * 100, object_pk="p" * 100, path="/" + "a" * 500, method="G" * 20)
        row = OperationLog.objects.get()
        assert len(row.module) == 64
        assert len(row.object_pk) == 64
        assert len(row.path) == 400
        assert len(row.method) == 8

    def test_creator_resolution_none_without_pk(self, db):
        record(module="test:audit", creator=None, modifier=object())
        row = OperationLog.objects.get(module="test:audit")
        assert row.creator is None
        assert row.modifier is None  # 无 pk 的对象按匿名处理

    def test_unserializable_changes_do_not_raise(self):
        """changes 含循环引用等不可序列化结构：只告警，不阻断业务（也不落半行）。"""
        payload: dict = {}
        payload["self"] = payload  # 循环引用：json.dumps 抛 ValueError
        record(module="test:audit", changes=payload)
        assert not OperationLog.objects.filter(module="test:audit").exists()

    def test_defaults_write_minimal_row(self, db):
        record(module="test:audit")
        row = OperationLog.objects.get(module="test:audit")
        assert row.object_pk is None
        assert row.changes is None
        assert row.creator is None
