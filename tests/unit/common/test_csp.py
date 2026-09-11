# -*- coding: utf-8 -*-
"""S3 CSP 落地（django-csp + 运行期模式开关）：策略头 / 模式切换 / 上报端点。

ADR-004 复审（Django 6.2 升级取消）后改为独立方案 django-csp；默认 report-only
观察一周再切 enforce，模式与上报地址通过系统配置运行期调整，不需要重新发版。
"""

import json
import logging

import pytest

from common.core.config import SysConfig
from common.core.middleware import CSP_HEADER, CSP_HEADER_REPORT_ONLY
from common.api.csp import CSP_REPORT_LOG_THROTTLE_SECONDS, CSP_REPORT_HEADER

pytestmark = pytest.mark.django_db

HEALTH_URL = "/api/common/api/health"
REPORT_URL = "/api/common/api/csp-report"


def patch_config(monkeypatch, key, value):
    monkeypatch.setattr(type(SysConfig), key, property(lambda self: value), raising=False)


class TestCSPHeaders:
    def test_default_report_only(self, api_client):
        """默认 report-only：只下发观察头，不拦截（enforce 头不存在）。"""
        response = api_client.get(HEALTH_URL)
        assert CSP_HEADER_REPORT_ONLY in response.headers
        assert CSP_HEADER not in response.headers
        policy = response.headers[CSP_HEADER_REPORT_ONLY]
        assert "default-src 'self'" in policy
        assert "object-src 'none'" in policy
        assert "frame-ancestors 'self'" in policy

    def test_enforce_mode(self, api_client, monkeypatch):
        """enforce：观察头改写为强制头（前端不回归的前提下才可切）。"""
        patch_config(monkeypatch, "CSP_MODE", "enforce")
        response = api_client.get(HEALTH_URL)
        assert CSP_HEADER in response.headers
        assert CSP_HEADER_REPORT_ONLY not in response.headers

    def test_disabled_mode(self, api_client, monkeypatch):
        patch_config(monkeypatch, "CSP_MODE", "disabled")
        response = api_client.get(HEALTH_URL)
        assert CSP_HEADER not in response.headers
        assert CSP_HEADER_REPORT_ONLY not in response.headers

    def test_report_uri_injected(self, api_client, monkeypatch):
        patch_config(monkeypatch, "CSP_REPORT_URI", "/api/common/api/csp-report")
        policy = api_client.get(HEALTH_URL).headers[CSP_HEADER_REPORT_ONLY]
        assert policy.endswith("report-uri /api/common/api/csp-report")

    def test_static_prefix_excluded(self, api_client):
        """/media 与静态资源前缀豁免（大文件/媒体响应不背策略头）。"""
        response = api_client.get("/media/not-exists.txt")
        assert CSP_HEADER_REPORT_ONLY not in response.headers


class TestCSPReportEndpoint:
    def test_report_logged_and_throttled(self, api_client, caplog):
        payload = {
            "csp-report": {
                "document-uri": "https://example.com/#/system/user/index",
                "violated-directive": "script-src 'self'",
                "blocked-uri": "https://cdn.example.com/x.js",
            }
        }
        with caplog.at_level(logging.WARNING, logger="xadmin"):
            first = api_client.post(REPORT_URL, data=json.dumps(payload), content_type="application/csp-report")
            assert first.status_code == 204
            assert first[CSP_REPORT_HEADER] == "logged"
            lines = [record for record in caplog.records if "CSP violation" in record.getMessage()]
            assert len(lines) == 1
            # 同 指令+文档 在节流窗口内只记一次
            api_client.post(REPORT_URL, data=json.dumps(payload), content_type="application/csp-report")
            lines = [record for record in caplog.records if "CSP violation" in record.getMessage()]
            assert len(lines) == 1
        assert CSP_REPORT_LOG_THROTTLE_SECONDS >= 60

    def test_report_accepts_csp3_envelope_and_garbage(self, api_client):
        """兼容 CSP3 数组信封与非法 JSON（不 500）。"""
        csp3 = {"reports": [{"type": "csp-violation", "body": {"documentURL": "https://x/", "blockedURL": "blob:"}}]}
        assert (
            api_client.post(REPORT_URL, data=json.dumps(csp3), content_type="application/reports+json").status_code
            == 204
        )
        assert api_client.post(REPORT_URL, data="{not-json", content_type="application/csp-report").status_code == 204
