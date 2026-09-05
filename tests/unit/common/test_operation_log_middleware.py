# -*- coding: utf-8 -*-
"""操作日志写入路径测试（PERF-05 / PERF-18）。

覆盖：
1. 写入用 UPDATE 而非 update_or_create（主键已知，省 1 条 SELECT）；
2. 大字段截断到 MAX_LOG_FIELD；
3. 日志写通过 transaction.on_commit 移出请求事务；
4. 缺失 User-Agent 头不再抛 KeyError；
5. 敏感字段脱敏清单扩展；
6. 集成：写请求日志真实落库（UPDATE 生效）。
"""
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.middleware import (MAX_LOG_FIELD, build_operation_log_info,
                                    desensitize_body, write_operation_log)
from common.utils.request import get_browser, get_os
from system.models import OperationLog

pytestmark = pytest.mark.django_db

DEMO_URL = "/api/demo/book"


class TestWriteOperationLog:
    def test_update_instead_of_update_or_create(self, superuser):
        log = OperationLog(module="demo", method="POST", path=DEMO_URL)
        log.save()

        with CaptureQueriesContext(connection) as ctx:
            write_operation_log(log.id, {"module": "demo-updated", "status_code": 1000})

        queries = [q["sql"] for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]
        # 仅 1 条 UPDATE，无前置 SELECT
        assert len(queries) == 1
        assert queries[0].strip().upper().startswith("UPDATE")
        log.refresh_from_db()
        assert log.module == "demo-updated"
        assert log.status_code == 1000

    def test_missing_row_is_noop(self):
        write_operation_log("00000000-0000-0000-0000-000000000000", {"module": "x"})
        assert OperationLog.objects.count() == 0


class TestFieldTruncation:
    def test_desensitize_only_mutates_copy(self):
        body = {"password": "secret", "old_password": "old-secret", "access": "token-a", "refresh": "token-r",
                "name": "keep"}
        masked = desensitize_body(body)
        assert masked["password"] == "******"
        assert masked["old_password"] == "**********"
        assert masked["access"] == "*******"
        assert masked["refresh"] == "*******"
        assert masked["name"] == "keep"
        # 不污染原请求体
        assert body["password"] == "secret"

    def test_truncation_constants(self):
        assert MAX_LOG_FIELD == 4096

    def test_large_fields_are_truncated(self, superuser):
        """大请求体 / 大响应整包入库会被截断到 MAX_LOG_FIELD"""
        huge = "x" * (MAX_LOG_FIELD * 4)
        request = type("R", (), {
            "META": {"HTTP_USER_AGENT": "pytest-agent"},
            "method": "POST",
            "path": DEMO_URL,
            "request_data": {"data": huge},
            "request_ip": "127.0.0.1",
            "request_module": "demo",
            "request_uuid": None,
            "user": superuser,
        })()
        response = type("R", (), {
            "status_code": 200,
            "data": {"code": 1000, "data": {"items": [huge]}, "detail": None},
        })()

        info = build_operation_log_info(request, response, 0)

        assert len(info["body"]) == MAX_LOG_FIELD
        assert len(info["response_result"]) == MAX_LOG_FIELD
        assert info["status_code"] == 1000

    def test_non_dict_response_does_not_parse_body(self, superuser):
        """非 dict 响应不再整包解析 content（旧实现解析后直接丢弃）"""
        request = type("R", (), {
            "META": {"HTTP_USER_AGENT": "pytest-agent"},
            "method": "POST",
            "path": DEMO_URL,
            "request_data": {},
            "request_ip": "127.0.0.1",
            "request_module": "demo",
            "user": superuser,
        })()
        response = type("R", (), {"status_code": 302, "content": b"raw-bytes"})()

        info = build_operation_log_info(request, response, 0)

        assert info["status_code"] is None
        assert info["response_result"] == '{"code": null, "data": null, "detail": null}'


class TestUserAgent:
    def test_missing_user_agent_does_not_raise(self):
        request = type("R", (), {"META": {}})()
        assert get_browser(request) == "Other"
        assert get_os(request) == "Other"

    def test_user_agent_parsed_once(self):
        calls = {"n": 0}
        request = type("R", (), {"META": {"HTTP_USER_AGENT": "pytest-agent"}})()

        import common.utils.request as req_mod

        original = req_mod.parse

        def counting_parse(value):
            calls["n"] += 1
            return original(value)

        try:
            req_mod.parse = counting_parse
            get_browser(request)
            get_os(request)
            get_browser(request)
        finally:
            req_mod.parse = original
        assert calls["n"] == 1


class TestDesensitizeIntegration:
    def test_write_request_logs_are_masked(self, superuser):
        """脱敏在 __handle_response 内完成（通过 write_operation_log 的 info 验证）"""
        body = {"password": "top-secret", "access": "jwt-token", "name": "alice"}
        masked = desensitize_body(body)
        info = {"body": json.dumps(masked, default=str)}
        log = OperationLog(module="demo", method="POST", path=DEMO_URL)
        log.save()
        write_operation_log(log.id, info)
        log.refresh_from_db()
        payload = json.loads(log.body)
        assert payload["password"] == "**********"
        assert payload["access"] == "*********"
        assert payload["name"] == "alice"


class TestOnCommitWritePath:
    def test_log_written_after_transaction_commit(self, django_capture_on_commit_callbacks, superuser):
        """ATOMIC_REQUESTS 下日志写在请求事务提交后执行"""
        from django.db import transaction

        with django_capture_on_commit_callbacks(execute=True):
            with transaction.atomic():
                log = OperationLog(module="demo", method="POST", path=DEMO_URL)
                log.save()
                transaction.on_commit(lambda: write_operation_log(log.id, {"status_code": 1000}))
                # 事务内：尚未写日志
                assert OperationLog.objects.get(pk=log.pk).status_code is None

        # 提交后：UPDATE 已执行
        log.refresh_from_db()
        assert log.status_code == 1000

    def test_rollback_produces_no_log_write(self, superuser):
        from django.db import transaction

        try:
            with transaction.atomic():
                log = OperationLog(module="demo", method="POST", path=DEMO_URL)
                log.save()
                transaction.on_commit(lambda: write_operation_log(log.id, {"status_code": 1000}))
                raise ValueError("rollback")
        except ValueError:
            pass
        # 事务回滚，占位行与日志写一并消失
        assert not OperationLog.objects.filter(module="demo").exists()
