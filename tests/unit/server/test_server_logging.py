# -*- coding: utf-8 -*-
"""日志格式器的请求上下文健壮性（2026-09-18 真丢包演练发现的 Logging error 修复）。

背景：``ServerFormatter`` / ``JsonFormatter`` 直接访问 ``current_request.user``——
认证中间件之前的异常路径（DisallowedHost、请求初始化失败）请求没有 ``user`` 属性，
格式器抛 AttributeError 导致 ``Logging error``、整条日志记录（含 500 的 traceback）
被吞掉。守护：两种格式器都必须对「有 request、无 user」兜底为 SYSTEM。
"""

import json
import logging
from types import SimpleNamespace

from server import logging as server_logging


def _make_record():
    return logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)


class TestFormatterWithoutUserAttribute:
    def test_server_formatter_falls_back_to_system(self, monkeypatch):
        # 认证中间件之前的请求：有 request、无 user 属性
        monkeypatch.setattr(server_logging, "get_current_request", lambda: SimpleNamespace())
        formatter = server_logging.ServerFormatter("%(requestUser)s|%(requestUuid)s|%(message)s")
        out = formatter.format(_make_record())
        assert out.startswith("SYSTEM|")
        assert out.endswith("|hello")

    def test_json_formatter_falls_back_to_system(self, monkeypatch):
        monkeypatch.setattr(server_logging, "get_current_request", lambda: SimpleNamespace())
        formatter = server_logging.JsonFormatter()
        payload = json.loads(formatter.format(_make_record()))
        assert payload["request_user"] == "SYSTEM"

    def test_formatters_still_resolve_real_user(self, monkeypatch):
        request = SimpleNamespace(user="alice", request_uuid="uuid-1")
        monkeypatch.setattr(server_logging, "get_current_request", lambda: request)
        formatter = server_logging.ServerFormatter("%(requestUser)s|%(requestUuid)s")
        assert formatter.format(_make_record()) == "alice|uuid-1"
