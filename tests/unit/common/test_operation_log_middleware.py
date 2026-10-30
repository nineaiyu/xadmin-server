# -*- coding: utf-8 -*-
"""操作日志写入路径测试。

覆盖：
1. 落库用单条 INSERT（响应阶段一次性写，无占位行 / 无 UPDATE 回填）；
2. 大字段截断到 OPERATION_LOG_FIELD_MAX（默认 4096，可配置，0 = 不落）；
3. 写路径独立于请求事务（process_view 在 ATOMIC_REQUESTS 事务外，响应阶段
   同样在事务外；异常 / 回滚路径下日志行照写，见 TestResponseTimeWritePath）；
4. 缺失 User-Agent 头不再抛 KeyError；
5. 敏感字段脱敏清单扩展；
6. 集成：写请求日志真实落库（INSERT 生效，行字段完整）。
"""

import json
from typing import Any

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from audit.models import OperationLog
from common.core.middleware import (
    MAX_LOG_FIELD,
    build_operation_log_info,
    desensitize_body,
    insert_operation_log,
    mark_sensitive_value_fields,
    request_value_fields,
)
from common.utils.request import get_browser, get_os
from demo.models import Book
from demo.serializers.book import BookSerializer

pytestmark = pytest.mark.django_db

DEMO_URL = "/api/demo/book"

# --- 真实链路探针视图（占位行事务边界结论的守护，见 TestResponseTimeWritePath） ---
# 视图内抛未预期异常：经本项目统一异常处理器（common_exception_handler）兜成
# 500 响应、不向外传播——用于验证异常路径下日志行同样完整落库。


class _BoomProbeViewSet(viewsets.ModelViewSet):
    """探针：create 抛未预期异常。"""

    queryset = Book.objects.all()
    serializer_class = BookSerializer
    permission_classes = [AllowAny]

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("probe boom")


class _ObserveProbeViewSet(viewsets.ModelViewSet):
    """探针：记录视图执行期间已存在的日志行数（半成品行守护）。"""

    queryset = Book.objects.all()
    serializer_class = BookSerializer
    permission_classes = [AllowAny]

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        OBSERVED["rows_during_view"] = OperationLog.objects.count()
        return Response({"code": 1000, "detail": "ok"})


OBSERVED: dict[str, Any] = {}

#: 探针路由：仅在本文件作为 ROOT_URLCONF 时生效（override_settings 指定）
urlpatterns = [
    path("api/probe/boom/", _BoomProbeViewSet.as_view({"post": "create"})),
    path("api/probe/observe/", _ObserveProbeViewSet.as_view({"post": "create"})),
]


class TestInsertOperationLog:
    @staticmethod
    def _fake_request(user=None):
        return type(
            "R",
            (),
            {
                "META": {"HTTP_USER_AGENT": "pytest-agent"},
                "method": "POST",
                "path": DEMO_URL,
                "request_data": {"name": "x"},
                "request_ip": "127.0.0.1",
                "request_module": "demo",
                "request_uuid": None,
                "user": user,
            },
        )()

    def test_single_insert_no_update(self, superuser):
        info = build_operation_log_info(
            self._fake_request(superuser),
            type("R", (), {"status_code": 200, "data": {"code": 1000}})(),
            0,
        )

        with CaptureQueriesContext(connection) as ctx:
            insert_operation_log(info)

        writes = [q["sql"] for q in ctx.captured_queries if q["sql"].strip().upper().startswith(("INSERT", "UPDATE"))]
        # 单条 INSERT 落库，无占位行 UPDATE 回填（pre_save 信号的 creator 懒加载 SELECT 不计）
        assert len(writes) == 1
        assert writes[0].strip().upper().startswith("INSERT")
        row = OperationLog.objects.get(path=DEMO_URL)
        assert row.method == "POST"
        assert row.status_code == 1000
        assert row.creator_id == superuser.pk

    def test_insert_failure_does_not_raise(self, monkeypatch):
        """写失败只告警不抛出：日志是旁路能力，不能把响应打成 500。"""
        from common.core import oplog_recorder

        class _BoomManager:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("db down")

        class _BoomModel:
            objects = _BoomManager

        monkeypatch.setattr(oplog_recorder, "OperationLog", _BoomModel)
        assert insert_operation_log({"module": "x"}) is None


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

    def test_value_only_config_patch_masked_by_request_marker(self):
        """PATCH /api/system/config/{pk} 只带 value：敏感键在上游行，按值语义脱敏。"""
        body = {"value": "scim-plain-token", "is_active": True}
        assert desensitize_body(body)["value"] == "scim-plain-token"  # 无上下文时原样
        masked = desensitize_body(body, ("value", "detail"))
        assert masked["value"] == "*" * len("scim-plain-token")
        assert masked["is_active"] is True

    def test_request_marker_is_shared_with_drf_request(self):
        """DRF Request 的标记必须落到原生 HttpRequest（中间件读原始对象）。"""

        class _Raw:
            pass

        class _Drf:
            _request = _Raw()

        mark_sensitive_value_fields(_Drf(), ("value",))
        mark_sensitive_value_fields(_Drf(), ("detail",))
        assert request_value_fields(_Drf()._request) == ("detail", "value")
        # 无请求上下文时静默跳过（离线序列化 / 导入导出）
        mark_sensitive_value_fields(None, ("value",))


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
                "path": "/api/identity/auth/token",
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
        from identity.models.token import PersonalAccessToken

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
        """脱敏在 __handle_response 内完成（通过 insert_operation_log 的 info 验证）"""
        body = {"password": "top-secret", "access": "jwt-token", "name": "alice"}
        masked = desensitize_body(body)
        info = {"body": json.dumps(masked, default=str)}
        insert_operation_log({**info, "module": "demo", "method": "POST", "path": DEMO_URL})
        log = OperationLog.objects.get(path=DEMO_URL)
        payload = json.loads(log.body)
        assert payload["password"] == "**********"
        assert payload["access"] == "*********"
        assert payload["name"] == "alice"


class TestValueScalarDesensitization:
    """脱敏覆盖「键值对 value/detail」与「键名命中敏感模式」形态。

    键名精确匹配只收 SENSITIVE_FIELDS 名单键；SystemConfig 的标量凭据以
    {"key": "SCIM_TOKEN", "value": "<明文>"} 键值对（或凭据自身名作 JSON 键）
    出现时同样是凭据本体，漏掩即全站凭据明文进操作日志（日志导出 = 凭据外带）。
    """

    def test_system_config_pair_value_masked(self):
        """SystemConfig 写入体的 value 是凭据本体：key 命中整值加密键即掩码。"""
        masked = desensitize_body({"key": "SCIM_TOKEN", "value": "tok-plain-value"})
        assert masked["value"] == "*" * len("tok-plain-value")
        assert "tok-plain-value" not in json.dumps(masked)

    def test_system_config_pair_detail_masked(self):
        masked = desensitize_body({"key": "OPS_ALERT_TOKEN", "detail": "detail-carries-secret"})
        assert masked["detail"] == "*" * len("detail-carries-secret")

    def test_pair_non_sensitive_key_kept(self):
        """key 非敏感配置键：value 照常保留（业务键值对不受影响）。"""
        masked = desensitize_body({"key": "SITE_TITLE", "value": "xadmin"})
        assert masked["value"] == "xadmin"

    def test_credential_name_key_masked(self):
        """凭据以自身名为 JSON 键出现（如 {"scim_token": ...}）同样收敛。"""
        masked = desensitize_body({"scim_token": "tok-abc", "FILE_S3_SECRET_KEY": "s3-secret", "name": "keep"})
        assert masked["scim_token"] == "*" * len("tok-abc")
        assert masked["FILE_S3_SECRET_KEY"] == "*" * len("s3-secret")
        assert masked["name"] == "keep"

    def test_numeric_counter_not_masked(self):
        """命中模式的数值计数器（max_tokens 等）不受影响——模式口径只收字符串值。"""
        masked = desensitize_body({"max_tokens": 2048, "AI_MAX_TOKENS": 4096})
        assert masked["max_tokens"] == 2048
        assert masked["AI_MAX_TOKENS"] == 4096

    def test_scim_token_write_log_has_no_plaintext(self, auth_client):
        """part5 验证口径：写 SCIM_TOKEN 后查 OperationLog.body 不含明文。"""
        from system.models import SystemConfig

        secret = "scim-bearer-secret-9f2c"
        resp = auth_client.post(
            "/api/system/config/system",
            {"key": "SCIM_TOKEN", "value": secret, "is_active": True},
            format="json",
        )
        assert resp.status_code == 200, getattr(resp, "data", resp.content)
        assert SystemConfig.objects.filter(key="SCIM_TOKEN").exists()

        log = (
            OperationLog.objects.filter(method="POST", path="/api/system/config/system")
            .order_by("-created_time")
            .first()
        )
        assert log is not None, "写配置请求应落操作日志"
        assert secret not in (log.body or "")
        payload = json.loads(log.body)
        assert payload["value"] == "*" * len(secret)


class TestResponseTimeWritePath:
    """守护：日志改为「process_view 只标记、process_response 单条 INSERT」。

    事务边界结论（Django 源码核对 + 真实链路实证）：``BaseHandler._get_response``
    先执行 view middleware（``process_view``）再 ``make_view_atomic`` 包视图——
    ``process_view`` 在 ATOMIC_REQUESTS 事务**之外**，旧「占位行 + on_commit
    UPDATE」方案在此写库即 autocommit 立即提交，与请求事务无关（包一层 atomic
    也没有外层事务可加入）；响应阶段同样在事务外，``transaction.on_commit`` 在
    autocommit 下**立即执行**而非「提交后」。本项目统一异常处理器把视图内未预期
    异常兜成 500 响应（不向外传播），请求事务随之正常提交——「视图异常逃逸成
    孤儿行」在 DRF 面上不会发生，但旧实现的多处注释与守护测试描述的并非真实
    执行路径。新实现把行装配收敛到响应阶段一次 INSERT：行要么完整出现要么不
    出现，孤儿行 / 半成品行结构性消除，且每请求少一次数据库往返。
    """

    @override_settings(ROOT_URLCONF=__name__)
    def test_success_row_written_immediately(self, client, superuser):
        """成功请求：process_response 返回时行已完整落库（不依赖任何提交钩子）。"""
        before = OperationLog.objects.count()
        resp = client.post("/api/probe/observe/", {}, content_type="application/json")
        assert resp.status_code == 200
        # 视图执行期间不存在半成品行（并发读方不可见；旧实现此刻已有占位行）
        assert OBSERVED["rows_during_view"] == before
        assert OperationLog.objects.count() == before + 1
        row = OperationLog.objects.order_by("-created_time").first()
        assert row.method == "POST"
        assert row.path == "/api/probe/observe/"
        assert row.response_code == 200
        assert row.status_code == 1000
        assert row.module

    @override_settings(ROOT_URLCONF=__name__)
    def test_view_exception_row_filled_not_orphan(self, client):
        """视图内未预期异常 → 500 响应 → 行完整落库（非 module-only 孤儿行）。"""
        before = OperationLog.objects.count()
        resp = client.post("/api/probe/boom/", {}, content_type="application/json")
        assert resp.status_code == 500
        assert OperationLog.objects.count() == before + 1
        row = OperationLog.objects.order_by("-created_time").first()
        assert row.response_code == 500
        assert row.method == "POST"
        assert row.path == "/api/probe/boom/"
        assert row.module  # 视图集 docstring 首行，非空即非孤儿占位行

    def test_row_written_after_request_transaction_rollback(self, auth_client):
        """DRF 校验失败触发 set_rollback（请求事务回滚）：日志行照写。

        旧注释声称「请求事务回滚时占位行一并消失」——真实执行中占位行写在事务
        外、回滚带不走它；新实现在响应阶段（事务外）INSERT，回滚同样不影响日志
        落库。失败请求留痕是审计语义，不是缺陷。
        """
        resp = auth_client.post(DEMO_URL, {}, format="json")
        assert resp.status_code == 400
        row = OperationLog.objects.filter(path=DEMO_URL, method="POST").order_by("-created_time").first()
        assert row is not None
        assert row.response_code == 400


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
        # 惰性解析：DEBUG 关闭且非操作日志/慢请求时不再为预览复制完整请求体；
        # 真正需要正文的路径（操作日志装配 / 慢请求）在消费点按需解析。
        assert not hasattr(request, "request_data")

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
    ``SENSITIVE_GET_ACTIONS`` 声明（沿 MRO 并集），中间件单独放行。
    """

    def test_export_data_get_creates_log(self, auth_client, superuser):
        from demo.models import Book

        Book.objects.create(name="审计书", isbn="i-o8-5", author="a", admin=superuser, admin2=superuser)
        before = OperationLog.objects.count()
        resp = auth_client.get(f"{DEMO_URL}/export-data?type=xlsx")
        assert resp.status_code == 200
        assert OperationLog.objects.count() == before + 1
        # 响应阶段单条 INSERT：行字段完整（旧实现在 pytest 事务内只落占位行）
        row = OperationLog.objects.order_by("-created_time").first()
        assert row.method == "GET"
        assert "export-data" in row.path
        assert row.response_code == 200
        assert row.status_code == 1000

    def test_list_get_not_logged(self, auth_client, superuser):
        before = OperationLog.objects.count()
        resp = auth_client.get(DEMO_URL)
        assert resp.status_code == 200
        assert OperationLog.objects.count() == before

    def test_sensitive_get_actions_mro_union(self):
        """声明沿 MRO 取并集：导出 mixin 声明 export_data，下载 mixin 声明 download。"""
        from common.core.oplog_recorder import sensitive_get_actions
        from demo.views import BookViewSet
        from task.views.admin.export import ExportRecordViewSet
        from task.views.admin.import_ import ImportRecordViewSet

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
