# -*- coding: utf-8 -*-
"""操作日志写入路径测试。

覆盖：
1. 写入用 UPDATE 而非 update_or_create（主键已知，省 1 条 SELECT）；
2. 大字段截断到 OPERATION_LOG_FIELD_MAX（默认 4096，可配置，0 = 不落）；
3. 日志写通过 transaction.on_commit 移出请求事务；
4. 缺失 User-Agent 头不再抛 KeyError；
5. 敏感字段脱敏清单扩展；
6. 集成：写请求日志真实落库（UPDATE 生效）。
"""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.middleware import MAX_LOG_FIELD, build_operation_log_info, desensitize_body, write_operation_log
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
        body = {
            "password": "secret",
            "old_password": "old-secret",
            "access": "token-a",
            "refresh": "token-r",
            "name": "keep",
        }
        masked = desensitize_body(body)
        assert masked["password"] == "******"
        assert masked["old_password"] == "**********"
        assert masked["access"] == "*******"
        assert masked["refresh"] == "*******"
        assert masked["name"] == "keep"
        # 不污染原请求体
        assert body["password"] == "secret"

    def test_temp_token_fields_masked(self):
        """登录/绑定加密握手的临时令牌与验证码票据不落明文。"""
        body = {"token": "tmp_token_abc", "verify_token": "vt-abc", "username": "alice"}
        masked = desensitize_body(body)
        assert masked["token"] == "*" * len("tmp_token_abc")
        assert masked["verify_token"] == "*" * len("vt-abc")
        assert masked["username"] == "alice"

    def test_desensitize_recurses_nested_containers(self):
        """嵌套 dict / list 里的敏感键同样收敛（审批 payload、批量提交体等结构）。"""
        body = {
            "items": [
                {"password": "s1", "name": "a"},
                {"token": "tmp_token_x", "nested": {"refresh": "r1"}},
            ],
            "plain": "keep",
        }
        masked = desensitize_body(body)
        assert masked["items"][0]["password"] == "**"
        assert masked["items"][0]["name"] == "a"
        assert masked["items"][1]["token"] == "*" * len("tmp_token_x")
        assert masked["items"][1]["nested"]["refresh"] == "**"
        assert masked["plain"] == "keep"
        # 原始结构不被污染（深拷贝语义）
        assert body["items"][0]["password"] == "s1"

    def test_desensitize_non_container_passthrough(self):
        assert desensitize_body("raw") == "raw"
        assert desensitize_body(None) is None


class TestResponseDesensitization:
    """响应快照与请求体同口径：登录 access/refresh 与临时令牌 token 不落操作日志。"""

    @staticmethod
    def _build(response_data):
        request = type(
            "R",
            (),
            {
                "META": {"HTTP_USER_AGENT": "pytest-agent"},
                "method": "POST",
                "path": "/api/system/auth/token",
                "request_data": {},
                "request_ip": "127.0.0.1",
                "request_module": "auth",
                "request_uuid": None,
                "user": None,
            },
        )()
        response = type("R", (), {"status_code": 200, "data": response_data})()
        return build_operation_log_info(request, response, 0)

    def test_response_result_masks_credentials(self):
        info = self._build(
            {
                "code": 1000,
                "data": {"token": "tmp_token_abc", "access": "eyJ.access", "refresh": "eyJ.refresh"},
                "detail": None,
            }
        )
        payload = json.loads(info["response_result"])
        assert payload["data"]["token"] == "*" * len("tmp_token_abc")
        assert payload["data"]["access"] == "*" * len("eyJ.access")
        assert payload["data"]["refresh"] == "*" * len("eyJ.refresh")
        assert "tmp_token_abc" not in info["response_result"]

    def test_response_result_keeps_business_data(self):
        info = self._build({"code": 1000, "data": {"count": 3, "items": [{"name": "x"}]}, "detail": None})
        payload = json.loads(info["response_result"])
        assert payload["data"] == {"count": 3, "items": [{"name": "x"}]}

    def test_truncation_constants(self):
        assert MAX_LOG_FIELD == 4096

    def test_large_fields_are_truncated(self, superuser):
        """大请求体 / 大响应整包入库会被截断到 MAX_LOG_FIELD"""
        huge = "x" * (MAX_LOG_FIELD * 4)
        request = type(
            "R",
            (),
            {
                "META": {"HTTP_USER_AGENT": "pytest-agent"},
                "method": "POST",
                "path": DEMO_URL,
                "request_data": {"data": huge},
                "request_ip": "127.0.0.1",
                "request_module": "demo",
                "request_uuid": None,
                "user": superuser,
            },
        )()
        response = type(
            "R",
            (),
            {
                "status_code": 200,
                "data": {"code": 1000, "data": {"items": [huge]}, "detail": None},
            },
        )()

        info = build_operation_log_info(request, response, 0)

        assert len(info["body"]) == MAX_LOG_FIELD
        assert len(info["response_result"]) == MAX_LOG_FIELD
        assert info["status_code"] == 1000

    def test_non_dict_response_does_not_parse_body(self, superuser):
        """非 dict 响应不再整包解析 content（旧实现解析后直接丢弃）"""
        request = type(
            "R",
            (),
            {
                "META": {"HTTP_USER_AGENT": "pytest-agent"},
                "method": "POST",
                "path": DEMO_URL,
                "request_data": {},
                "request_ip": "127.0.0.1",
                "request_module": "demo",
                "user": superuser,
            },
        )()
        response = type("R", (), {"status_code": 302, "content": b"raw-bytes"})()

        info = build_operation_log_info(request, response, 0)

        assert info["status_code"] is None
        assert info["response_result"] == '{"code": null, "data": null, "detail": null}'


class TestConfigurableFieldLimit:
    """大字段上限走系统配置 OPERATION_LOG_FIELD_MAX（冗余正文可裁剪，0 = 不落）。"""

    @staticmethod
    def _build(monkeypatch, limit):
        from common.core.config import SysConfig

        monkeypatch.setattr(type(SysConfig), "OPERATION_LOG_FIELD_MAX", property(lambda self: limit), raising=False)
        request = type(
            "R",
            (),
            {
                "META": {"HTTP_USER_AGENT": "pytest-agent"},
                "method": "POST",
                "path": DEMO_URL,
                "request_data": {"data": "x" * 100},
                "request_ip": "127.0.0.1",
                "request_module": "demo",
                "request_uuid": None,
                "user": None,
            },
        )()
        response = type("R", (), {"status_code": 200, "data": {"code": 1000, "data": "y" * 100, "detail": None}})()
        return build_operation_log_info(request, response, 0)

    def test_custom_limit_applied(self, monkeypatch):
        info = self._build(monkeypatch, 8)
        assert len(info["body"]) == 8
        assert len(info["response_result"]) == 8

    def test_zero_limit_stores_no_content(self, monkeypatch):
        info = self._build(monkeypatch, 0)
        assert info["body"] == ""
        assert info["response_result"] == ""

    def test_invalid_config_falls_back_to_default(self, monkeypatch):
        """坏配置（非数字）回落默认值，不把日志组装打成 500（按默认 4096 原样保留）。"""
        info = self._build(monkeypatch, "not-a-number")
        assert info["body"] == json.dumps({"data": "x" * 100})

    def test_log_body_preview_masks_then_truncates(self, monkeypatch):
        """DEBUG / 慢请求日志正文：先脱敏再截断（截断不会把敏感串留在前缀）。"""
        from common.core.config import SysConfig
        from common.core.middleware import log_body_preview

        preview = log_body_preview({"password": "secret-value", "name": "abc"})
        assert "secret-value" not in preview
        assert '"name": "abc"' in preview

        monkeypatch.setattr(type(SysConfig), "OPERATION_LOG_FIELD_MAX", property(lambda self: 10), raising=False)
        truncated = log_body_preview({"field": "x" * 100})
        assert len(truncated) == 10

    def test_log_body_preview_zero_limit_stores_nothing(self, monkeypatch):
        from common.core.config import SysConfig
        from common.core.middleware import log_body_preview

        monkeypatch.setattr(type(SysConfig), "OPERATION_LOG_FIELD_MAX", property(lambda self: 0), raising=False)
        assert log_body_preview({"token": "tmp_token_x"}) == ""


class TestAuthIdentity:
    """凭证标识（PAT 精确审计）：auth_type / token_pk 落库字段。"""

    @staticmethod
    def _request(**extra):
        attrs = {
            "META": {"HTTP_USER_AGENT": "pytest-agent"},
            "method": "GET",
            "path": DEMO_URL,
            "request_data": {},
            "request_ip": "127.0.0.1",
            "request_module": "demo",
            "user": None,
        }
        attrs.update(extra)
        return type("R", (), attrs)()

    @staticmethod
    def _response(auth=..., status_code=200):
        """DRF 响应替身：renderer_context.request.auth 即认证链写入的凭证对象。"""
        attrs = {"status_code": status_code, "data": {"code": 1000}}
        if auth is not ...:
            attrs["renderer_context"] = {"request": type("Q", (), {"auth": auth})()}
        return type("R", (), attrs)()

    def test_pat_request_records_token_identity(self, superuser):
        from system.models.token import PersonalAccessToken

        token = PersonalAccessToken.objects.create(
            creator=superuser, name="ci", token_hash="a" * 64, token_prefix="pat_identity"
        )
        info = build_operation_log_info(self._request(user=superuser), self._response(token), 0)
        assert info["auth_type"] == OperationLog.AuthType.PAT
        assert info["token_pk"] == token.pk

    def test_failed_pat_auth_marks_type_without_token(self):
        """PAT 认证失败（401）拿不到 request.auth：只标类型，token_pk 留空。"""
        request = self._request(META={"HTTP_USER_AGENT": "pytest-agent", "HTTP_AUTHORIZATION": "Pat forged-value"})
        info = build_operation_log_info(request, self._response(auth=None, status_code=401), 0)
        assert info["auth_type"] == OperationLog.AuthType.PAT
        assert info["token_pk"] is None

    def test_jwt_and_anonymous_identity(self, superuser):
        # JWT：auth 是 token 对象但不是凭证实体
        info = build_operation_log_info(self._request(user=superuser), self._response(auth=object()), 0)
        assert info["auth_type"] == OperationLog.AuthType.JWT
        assert info["token_pk"] is None

        # 响应无渲染上下文（非 DRF 响应）时退化按用户判定
        info = build_operation_log_info(self._request(user=superuser), self._response(), 0)
        assert info["auth_type"] == OperationLog.AuthType.JWT

        # 匿名/白名单接口：留空
        info = build_operation_log_info(self._request(), self._response(), 0)
        assert info["auth_type"] is None
        assert info["token_pk"] is None


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


class TestLogLevelGuard:
    """/：日志正文与响应体的 eager 求值必须有 isEnabledFor 守卫。

    f-string 会先求值再按级别过滤：DEBUG 关闭时若仍走脱敏 + json.dumps（正文预览）
    或对 response.data 整体 repr（未开操作日志的每请求路径），开销白付。
    """

    @staticmethod
    def _silence_debug(monkeypatch):
        from common.core import middleware

        monkeypatch.setattr(middleware.logger, "isEnabledFor", lambda level: False)
        return middleware

    def test_request_preview_skipped_when_debug_disabled(self, monkeypatch):
        from django.test import RequestFactory

        from common.core.middleware import ApiLoggingMiddleware

        calls = []
        middleware = self._silence_debug(monkeypatch)
        monkeypatch.setattr(middleware, "log_body_preview", lambda payload: calls.append(payload) or "")

        request = RequestFactory().get("/api/demo/book", {"page": 1})
        ApiLoggingMiddleware(lambda r: None).process_request(request)

        assert calls == []
        # 契约不变：请求体照旧解析并挂到 request 上（审批指纹 / 刷新回退 / 操作日志消费）
        assert request.request_data == {"page": "1"}

    def test_request_preview_emitted_when_debug_enabled(self, monkeypatch):
        from django.test import RequestFactory

        from common.core import middleware as middleware_module
        from common.core.middleware import ApiLoggingMiddleware

        calls = []
        monkeypatch.setattr(middleware_module.logger, "isEnabledFor", lambda level: True)
        monkeypatch.setattr(middleware_module, "log_body_preview", lambda payload: calls.append(payload) or "PREVIEW")

        request = RequestFactory().get("/api/demo/book", {"page": 1})
        ApiLoggingMiddleware(lambda r: None).process_request(request)

        assert calls == [{"page": "1"}]

    def test_response_repr_skipped_when_debug_disabled(self, monkeypatch):
        """未开操作日志的请求：DEBUG 关闭时不得触碰 response.data（分页大响应 repr）。"""
        from django.test import RequestFactory

        from common.core.middleware import ApiLoggingMiddleware

        self._silence_debug(monkeypatch)

        class _BoomResponse:
            @property
            def data(self):
                raise AssertionError("DEBUG 关闭时不应读取 response.data")

        response = _BoomResponse()
        request = RequestFactory().get("/api/demo/book")
        assert ApiLoggingMiddleware(lambda r: None).process_response(request, response) is response


class TestSensitiveGetAudit:
    """敏感 GET（导出/下载）单列落操作日志。

    ``API_LOG_METHODS`` 默认不含 GET——导出/下载等敏感读取由视图侧
    ``SENSITIVE_GET_ACTIONS`` 声明（沿 MRO 并集），中间件单独放行；普通
    列表/详情 GET 维持不落库。pytest 事务内 on_commit 不回填，断言占位行。
    """

    def test_export_data_get_creates_log(self, auth_client, superuser):
        from demo.models import Book

        Book.objects.create(name="审计书", isbn="i-o8-5", author="a", admin=superuser, admin2=superuser)
        before = OperationLog.objects.count()
        resp = auth_client.get(f"{DEMO_URL}/export-data?type=xlsx")
        assert resp.status_code == 200
        assert OperationLog.objects.count() == before + 1

    def test_list_get_not_logged(self, auth_client, superuser):
        before = OperationLog.objects.count()
        resp = auth_client.get(DEMO_URL)
        assert resp.status_code == 200
        assert OperationLog.objects.count() == before

    def test_sensitive_get_actions_mro_union(self):
        """声明沿 MRO 取并集：导出 mixin 声明 export_data，下载 mixin 声明 download。"""
        from common.core.oplog_recorder import sensitive_get_actions
        from demo.views import BookViewSet
        from system.views.admin.export import ExportRecordViewSet
        from system.views.admin.import_ import ImportRecordViewSet

        assert sensitive_get_actions(BookViewSet) == frozenset({"export_data"})
        assert sensitive_get_actions(ExportRecordViewSet) == frozenset({"download"})
        assert sensitive_get_actions(ImportRecordViewSet) == frozenset({"download"})

    def test_should_log_decision(self, rf):
        from common.core.middleware import ApiLoggingMiddleware
        from demo.views import BookViewSet

        middleware = ApiLoggingMiddleware(lambda r: None)
        export_view = BookViewSet.as_view({"get": "export_data"})
        list_view = BookViewSet.as_view({"get": "list"})
        assert middleware._should_log(rf.get(f"{DEMO_URL}/export-data"), export_view) is True
        assert middleware._should_log(rf.get(DEMO_URL), list_view) is False
        assert middleware._should_log(rf.post(DEMO_URL, {}), list_view) is True

    def test_get_audit_flag_not_set_for_regular_methods(self, rf):
        """白名单路径才打 GET 审计标记：API_LOG_METHODS 命中的请求不带该标记。"""
        from common.core.middleware import ApiLoggingMiddleware
        from demo.views import BookViewSet

        middleware = ApiLoggingMiddleware(lambda r: None)
        request = rf.get(f"{DEMO_URL}/export-data")
        middleware.process_view(request, BookViewSet.as_view({"get": "export_data"}), (), {})
        assert getattr(request, "operation_log_get_audit", False) is True

        post_request = rf.post(DEMO_URL, {})
        middleware.process_view(post_request, BookViewSet.as_view({"post": "create"}), (), {})
        assert getattr(post_request, "operation_log_get_audit", False) is False
