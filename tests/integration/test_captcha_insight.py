# -*- coding: utf-8 -*-
"""验证码观测面 API：大盘聚合只读端点 + 日志只读列表（元数据协议）。"""

import pytest
from django.core.cache import cache

from captcha.models import CaptchaLog

pytestmark = pytest.mark.django_db

STATS_URL = "/api/settings/captcha/stats"
LOG_URL = "/api/settings/captcha/log"


@pytest.fixture(autouse=True)
def _clean_cache():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass
    yield


def _log(**fields):
    defaults = {"phase": "verify", "type": "slide", "passed": True, "scene": "login"}
    defaults.update(fields)
    return CaptchaLog.objects.create(**defaults)


class TestCaptchaStatsAPI:
    def test_returns_aggregated_payload(self, auth_client):
        _log(passed=True, decision="allow")
        _log(passed=False, decision="challenge")
        resp = auth_client.get(STATS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["window_hours"] == 24
        assert data["summary"]["verify"] == 2
        assert data["summary"]["pass_rate"] == 0.5
        assert data["by_decision"] == [
            {"decision": "allow", "total": 1},
            {"decision": "challenge", "total": 1},
        ]
        assert len(data["risk_distribution"]) == 5
        assert data["top_reasons"] == []
        assert data["top_ips"] == []
        assert data["policy"]["thresholds"] == {"allow": 20, "light": 40, "hard": 70, "deny": 90}
        assert data["policy"]["failure_ladder"] == "3,8,20"

    def test_accepts_window_parameter(self, auth_client):
        resp = auth_client.get(STATS_URL, {"hours": 168})
        assert resp.status_code == 200
        assert resp.data["data"]["window_hours"] == 168

    def test_rejects_out_of_range_window(self, auth_client):
        assert auth_client.get(STATS_URL, {"hours": 0}).status_code == 400
        assert auth_client.get(STATS_URL, {"hours": 169}).status_code == 400

    def test_anonymous_denied(self, api_client):
        assert api_client.get(STATS_URL).status_code in (401, 403)


class TestCaptchaLogAPI:
    def test_list_is_read_only_and_filterable(self, auth_client):
        _log(phase="verify", passed=True)
        _log(phase="challenge")
        resp = auth_client.get(LOG_URL, {"phase": "verify", "with_meta": "1"})
        assert resp.status_code == 200, resp.data
        payload = resp.data["data"]
        assert payload["total"] == 1
        assert payload["results"][0]["phase"]["value"] == "verify"
        # with_meta 内联元数据：列表页首开即可渲染列
        assert payload["search_columns"]
        assert payload["search_fields"]

    def test_search_columns_uses_table_fields(self, auth_client):
        resp = auth_client.get(f"{LOG_URL}/search-columns")
        assert resp.status_code == 200, resp.data
        fields = resp.data["data"]
        keys = {item["key"] for item in fields} if isinstance(fields, list) else set(fields)
        assert {"ts", "phase", "scene", "type", "passed", "latency_ms"} <= keys

    def test_write_endpoints_absent(self, auth_client):
        # 观测数据是审计痕迹：不提供删除 / 批量删除 / 新建端点
        assert auth_client.delete(f"{LOG_URL}/00000000-0000-0000-0000-000000000000/").status_code == 404
        assert auth_client.post(LOG_URL, {}, format="json").status_code == 405

    def test_anonymous_denied(self, api_client):
        assert api_client.get(LOG_URL).status_code in (401, 403)
