#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""服务启动自检的快速失败语义（2026-09-18 部署事故修复）。

背景：``hands.check_database_connection`` 以「check --database + expire_caches」轮询等待
数据库就绪；模型/索引级确定性错误（SystemCheckError，如索引名超长、GinIndex 缺
contrib.postgres）重试 60 次也不会自愈——实测把启动日志刷成噪音且 60s 后才退出，容器
在 restart 策略下反复挂死。守护：

- SystemCheckError → 立即退出（exit 12，仅尝试一次，不重试）；
- OperationalError → 保持既有重试语义（数据库未就绪属瞬态，60 次后 exit 10）。
"""

import pytest


class TestCheckDatabaseConnection:
    def _patch(self, monkeypatch, hands, effect):
        calls = []

        def fake_call_command(name, *args, **kwargs):
            calls.append(name)
            raise effect

        monkeypatch.setattr(hands.management, "call_command", fake_call_command)
        monkeypatch.setattr(hands.time, "sleep", lambda _seconds: None)
        return calls

    def test_system_check_error_fails_fast_without_retry(self, monkeypatch):
        from django.core.management.base import SystemCheckError

        from common.management.commands.services import hands

        calls = self._patch(monkeypatch, hands, SystemCheckError("boom: idx name too long"))

        with pytest.raises(SystemExit) as exc_info:
            hands.check_database_connection()
        assert exc_info.value.code == 12
        assert calls == ["check"], "确定性错误不得重试（60 次重试无意义且刷屏启动日志）"

    def test_operational_error_keeps_retry_then_exits(self, monkeypatch):
        from django.db.utils import OperationalError

        from common.management.commands.services import hands

        calls = self._patch(monkeypatch, hands, OperationalError("not ready"))

        with pytest.raises(SystemExit) as exc_info:
            hands.check_database_connection()
        assert exc_info.value.code == 10
        assert len(calls) == 60, "瞬态错误保持既有 60 次重试语义"
