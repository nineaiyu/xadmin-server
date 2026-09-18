# -*- coding: utf-8 -*-
"""S3 CSP 落地（django-csp + 运行期模式开关）：策略头 / 模式切换 / 上报端点。

原方案复审（Django 6.2 升级取消）后改为独立方案 django-csp；默认 report-only
观察一周再切 enforce，模式与上报地址通过系统配置运行期调整，不需要重新发版。
"""

import json
import logging
import re
from pathlib import Path

import pytest
from django.conf import settings as dj_settings

from common.api.csp import CSP_REPORT_HEADER, CSP_REPORT_LOG_THROTTLE_SECONDS, _synthetic_reason
from common.core.config import SysConfig
from common.core.middleware import CSP_HEADER, CSP_HEADER_REPORT_ONLY
from server.settings.base import _CSP_DIRECTIVES

pytestmark = pytest.mark.django_db

HEALTH_URL = "/api/common/api/health"
REPORT_URL = "/api/common/api/csp-report"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


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
    def test_report_logged_and_throttled(self, api_client, caplog, settings):
        """真实浏览器（有 UA + 本站 document-uri）的违规照常入统计。"""
        settings.ALLOWED_HOSTS = ["testserver"]
        payload = {
            "csp-report": {
                "document-uri": "https://testserver/#/system/user/index",
                "violated-directive": "script-src 'self'",
                "blocked-uri": "https://cdn.example.com/x.js",
            }
        }
        with caplog.at_level(logging.WARNING, logger="xadmin"):
            first = api_client.post(
                REPORT_URL,
                data=json.dumps(payload),
                content_type="application/csp-report",
                HTTP_USER_AGENT=BROWSER_UA,
            )
            assert first.status_code == 204
            assert first[CSP_REPORT_HEADER] == "logged"
            lines = [record for record in caplog.records if "CSP violation" in record.getMessage()]
            assert len(lines) == 1
            # 同 指令+文档 在节流窗口内只记一次
            api_client.post(
                REPORT_URL,
                data=json.dumps(payload),
                content_type="application/csp-report",
                HTTP_USER_AGENT=BROWSER_UA,
            )
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


class TestCSPSyntheticReport:
    """合成上报隔离（切 enforce 判据可判的前提）。

    背景：2026-09-15 生产日志中当日 69 条「CSP violation」全部来自探测/测试流量
    （`document=https://example.com/#/...`、`https://x/`、全空），真实浏览器违规为 0，
    导致「连续 7 天清零」的判据在隔离前不可判。合成上报只记 INFO，不进违规统计。
    """

    @pytest.mark.parametrize(
        "payload,user_agent,expected_reason",
        [
            ({"csp-report": {}}, BROWSER_UA, "missing-document-uri"),
            ({"csp-report": {"document-uri": "https://testserver/#/x"}}, "", "non-browser-agent"),
            (
                {"csp-report": {"document-uri": "https://testserver/#/x"}},
                "python-requests/2.31.0",
                "non-browser-agent",
            ),
            (
                {"csp-report": {"document-uri": "https://example.com/#/system/user/index"}},
                BROWSER_UA,
                "foreign-document-host",
            ),
        ],
    )
    def test_synthetic_reports_not_counted_as_violation(
        self, api_client, caplog, settings, payload, user_agent, expected_reason
    ):
        settings.ALLOWED_HOSTS = ["testserver"]
        with caplog.at_level(logging.INFO, logger="xadmin"):
            response = api_client.post(
                REPORT_URL,
                data=json.dumps(payload),
                content_type="application/csp-report",
                HTTP_USER_AGENT=user_agent,
            )
        assert response.status_code == 204
        assert response[CSP_REPORT_HEADER] == "ignored"
        assert not [record for record in caplog.records if "CSP violation" in record.getMessage()]
        ignored = [record for record in caplog.records if "CSP synthetic report ignored" in record.getMessage()]
        assert len(ignored) == 1
        assert f"reason={expected_reason}" in ignored[0].getMessage()

    def test_wildcard_allowed_hosts_skips_host_check(self, api_client, caplog, settings):
        """ALLOWED_HOSTS 为通配时无法判定归属，跳过该判据（宁可多记不漏记真实违规）。"""
        settings.ALLOWED_HOSTS = ["*"]
        payload = {
            "csp-report": {
                "document-uri": "https://example.org/#/system/user/index",
                "violated-directive": "img-src 'self'",
                "blocked-uri": "https://cdn.example.org/a.png",
            }
        }
        with caplog.at_level(logging.WARNING, logger="xadmin"):
            response = api_client.post(
                REPORT_URL,
                data=json.dumps(payload),
                content_type="application/csp-report",
                HTTP_USER_AGENT=BROWSER_UA,
            )
        assert response[CSP_REPORT_HEADER] == "logged"
        assert len([record for record in caplog.records if "CSP violation" in record.getMessage()]) == 1


class TestCSPPolicySync:
    """策略串三处同源守护：服务端 `_CSP_DIRECTIVES` ↔ 页面层 nginx ↔ 验证服务。

    页面层（SPA 文档）头由 `xadmin-web/default.conf` 下发（django-csp 覆盖不到文档层），
    `xadmin-client/scripts/csp-page-server.mjs` 的隔离验证服务内嵌同一串——任一处漂移都会让
    「隔离验证通过」与线上实际策略不一致，故固化比对。单仓检出（无同工作区兄弟仓）时跳过。
    """

    _PARITY_PLACES = (
        ("xadmin-web", "default.conf"),
        ("xadmin-client", "scripts/csp-page-server.mjs"),
    )

    @staticmethod
    def _expected() -> set:
        return {f"{key} {' '.join(values)}" for key, values in _CSP_DIRECTIVES.items()}

    @staticmethod
    def _parse_policy(policy: str) -> set:
        items = set()
        for part in policy.split(";"):
            part = part.strip()
            if not part or part.startswith("report-uri"):
                continue
            items.add(part)
        return items

    def _read(self, repo: str, relative: str):
        path = Path(dj_settings.PROJECT_DIR).parent / repo / relative
        if not path.exists():
            pytest.skip(f"单仓检出（缺 {repo}/{relative}），跳过三处同源比对")
        return path.read_text(encoding="utf-8")

    def test_nginx_page_layer_policy_matches(self):
        text = self._read(*self._PARITY_PLACES[0])
        match = re.search(r'add_header\s+Content-Security-Policy(?:-Report-Only)?\s+"([^"]+)"', text)
        assert match, "xadmin-web/default.conf 未找到页面层 CSP 头"
        assert self._parse_policy(match.group(1)) == self._expected(), (
            "页面层 CSP 策略串与服务端 _CSP_DIRECTIVES 漂移（改策略需三处同步）"
        )

    def test_client_harness_policy_matches(self):
        text = self._read(*self._PARITY_PLACES[1])
        match = re.search(r"const CSP = \[(.*?)\]\.join", text, re.S)
        assert match, "csp-page-server.mjs 未找到 CSP 串定义"
        policy = "; ".join(re.findall(r'"([^"]+)"', match.group(1)))
        assert self._parse_policy(policy) == self._expected(), (
            "CSP 隔离验证服务策略串与服务端 _CSP_DIRECTIVES 漂移（改策略需三处同步）"
        )


class TestSyntheticReasonHostBasis:
    """「本站」基准：上报请求自身 Host 优先（不依赖 ALLOWED_HOSTS 配置）。

    生产 `ALLOWED_HOSTS` 常为空（config.yml 默认注释掉），只靠它会让域名判据整体失效；
    report-uri 是同源相对路径，故 document-uri 应与上报请求同 host。
    """

    def test_request_host_decides_when_allowed_hosts_empty(self, settings):
        settings.ALLOWED_HOSTS = []
        assert _synthetic_reason("https://example.com/#/x", BROWSER_UA, "xadmin.example.com") == (
            "foreign-document-host"
        )
        assert _synthetic_reason("https://xadmin.example.com/#/x", BROWSER_UA, "xadmin.example.com") == ""

    def test_host_port_ignored(self, settings):
        """document-uri 与 Host 的端口差异不参与比较（同一站点不同入口）。"""
        settings.ALLOWED_HOSTS = []
        assert _synthetic_reason("https://xadmin.example.com/#/x", BROWSER_UA, "xadmin.example.com:8896") == ""

    def test_allowed_hosts_still_honored(self, settings):
        """多域名部署：ALLOWED_HOSTS 中的其它站点同样视为本站。"""
        settings.ALLOWED_HOSTS = ["xadmin.example.com", "ops.example.com"]
        assert _synthetic_reason("https://ops.example.com/#/x", BROWSER_UA, "xadmin.example.com") == ""
