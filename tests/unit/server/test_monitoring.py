#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可观测性初始化守护：Sentry 错误聚合 + 追踪（performance）的零开销与参数契约。

- DSN 未配置时不得初始化（无任何运行时开销）；
- DSN 配置时按 config.yml 参数初始化：environment / traces_sample_rate 透传、
  `send_default_pii=False`（不上报用户 PII）——追踪采样率默认 0.0（关闭），
  启用路径见 docs/ops/observability.md。
"""

import sys


class TestInitMonitoring:
    def test_no_dsn_is_zero_cost(self, monkeypatch):
        """无 DSN：不调用 sentry_sdk.init（零开销）。"""
        import sentry_sdk

        import server.monitoring as monitoring

        calls = []
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(monitoring.CONFIG, "SENTRY_DSN", "", raising=False)

        monitoring.init_monitoring()

        assert calls == [], "DSN 为空时不应初始化 Sentry"

    def test_dsn_initializes_with_expected_params(self, monkeypatch):
        """有 DSN：environment / traces_sample_rate 透传，PII 关闭。"""
        import sentry_sdk

        import server.monitoring as monitoring

        calls = []
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
        monkeypatch.setattr(monitoring.CONFIG, "SENTRY_DSN", "https://key@example.invalid/1", raising=False)
        monkeypatch.setattr(monitoring.CONFIG, "SENTRY_ENVIRONMENT", "test-env", raising=False)
        monkeypatch.setattr(monitoring.CONFIG, "SENTRY_TRACES_SAMPLE_RATE", 0.1, raising=False)

        monitoring.init_monitoring()

        assert len(calls) == 1
        kwargs = calls[0]
        assert kwargs["dsn"] == "https://key@example.invalid/1"
        assert kwargs["environment"] == "test-env"
        assert kwargs["traces_sample_rate"] == 0.1
        assert kwargs["send_default_pii"] is False, "禁止上报用户 PII"

    def test_sdk_missing_logs_and_skips(self, monkeypatch, caplog):
        """sentry-sdk 未安装时仅告警不抛错（可选依赖契约）。"""
        import server.monitoring as monitoring

        monkeypatch.setattr(monitoring.CONFIG, "SENTRY_DSN", "https://key@example.invalid/1", raising=False)
        # sys.modules 置 None 使 `import sentry_sdk` 抛 ImportError（标准模拟手法）
        monkeypatch.setitem(sys.modules, "sentry_sdk", None)
        try:
            with caplog.at_level("WARNING"):
                monitoring.init_monitoring()
        finally:
            sys.modules.pop("sentry_sdk", None)

        assert any("sentry-sdk is not installed" in record.message for record in caplog.records)
