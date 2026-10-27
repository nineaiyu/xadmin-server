# -*- coding: utf-8 -*-
"""风险策略设置端点：typed 配置 + 规则热更。"""

import pytest

from captcha.models import CaptchaRule

pytestmark = pytest.mark.django_db

RISK_URL = "/api/settings/captcha/risk"
RULES_URL = f"{RISK_URL}/rules"


class TestRiskSettings:
    def test_retrieve_returns_defaults(self, auth_client):
        resp = auth_client.get(RISK_URL)
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["CAPTCHA_RISK_MODE"] == "off"
        assert data["CAPTCHA_RISK_T_ALLOW"] == 20
        assert data["CAPTCHA_POW_BITS_HARD"] == 20
        assert data["CAPTCHA_FAIL_STEP"] == "3,8,20"

    def test_partial_update_persists(self, auth_client):
        resp = auth_client.patch(RISK_URL, {"CAPTCHA_RISK_T_HARD": 75, "CAPTCHA_RISK_MODE": "shadow"})
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["CAPTCHA_RISK_T_HARD"] == 75
        assert resp.data["data"]["CAPTCHA_RISK_MODE"] == "shadow"

    def test_rejects_invalid_thresholds(self, auth_client):
        resp = auth_client.patch(
            RISK_URL,
            {
                "CAPTCHA_RISK_T_ALLOW": 80,
                "CAPTCHA_RISK_T_LIGHT": 40,
                "CAPTCHA_RISK_T_HARD": 70,
                "CAPTCHA_RISK_T_DENY": 90,
            },
        )
        assert resp.status_code == 400

    def test_rejects_invalid_fail_step(self, auth_client):
        assert auth_client.patch(RISK_URL, {"CAPTCHA_FAIL_STEP": "3,8"}).status_code == 400

    def test_columns_expose_help_text(self, auth_client):
        resp = auth_client.get(f"{RISK_URL}/search-columns")
        assert resp.status_code == 200, resp.data
        raw = resp.data["data"]
        columns = raw if isinstance(raw, list) else raw.get("results", [])
        mapping = {item["key"]: item for item in columns}
        for key in (
            "CAPTCHA_RISK_MODE",
            "CAPTCHA_RISK_T_ALLOW",
            "CAPTCHA_RISK_T_LIGHT",
            "CAPTCHA_RISK_T_HARD",
            "CAPTCHA_RISK_T_DENY",
            "CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS",
            "CAPTCHA_RISK_DATACENTER_CIDRS",
            "CAPTCHA_RISK_PROXY_CIDRS",
            "CAPTCHA_POW_ENABLED",
            "CAPTCHA_POW_BITS_LIGHT",
            "CAPTCHA_POW_BITS_HARD",
            "CAPTCHA_POW_BITS_MAX",
            "CAPTCHA_POW_IP_BUDGET",
            "CAPTCHA_FAIL_STEP",
            "CAPTCHA_FAILURE_LINK_LOCK",
            "CAPTCHA_HONEYPOT_ENABLED",
            "CAPTCHA_ALERT_ENABLED",
            "CAPTCHA_ALERT_WINDOW_MINUTES",
            "CAPTCHA_ALERT_DENY_RATE",
            "CAPTCHA_ALERT_MIN_SAMPLES",
            "CAPTCHA_LOG_RETENTION_DAYS",
        ):
            assert mapping[key].get("help_text"), key


class TestRiskRules:
    def test_rules_are_seeded(self, auth_client):
        resp = auth_client.get(RULES_URL)
        assert resp.status_code == 200, resp.data
        rules = resp.data["data"]
        from captcha.risk import DEFAULT_RULES

        assert len(rules) == len(DEFAULT_RULES)
        assert {rule["code"] for rule in rules} >= {"env_headless", "honeypot", "hist_good"}

    def test_update_score_and_switch(self, auth_client):
        resp = auth_client.put(
            RULES_URL,
            {"rules": [{"code": "env_headless", "score": 70, "enabled": False}]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        rule = CaptchaRule.objects.get(code="env_headless")
        assert rule.score == 70
        assert rule.enabled is False

    def test_unknown_rule_is_rejected(self, auth_client):
        """过期/伪造 code 必须显式失败，不能静默 noop 后回显「已保存」。"""
        resp = auth_client.put(RULES_URL, {"rules": [{"code": "no_such_rule", "score": 10}]}, format="json")
        assert resp.status_code == 400
        assert "no_such_rule" in str(resp.data)
        assert not CaptchaRule.objects.filter(code="no_such_rule").exists()

    def test_score_is_bounded(self, auth_client):
        resp = auth_client.put(
            RULES_URL,
            {"rules": [{"code": "env_headless", "score": 1000}]},
            format="json",
        )
        assert resp.status_code == 400
        assert CaptchaRule.objects.get(code="env_headless").score != 1000

    def test_params_is_not_writable(self, auth_client):
        """params 无运行时消费点：不开放写入（可编辑但不生效会误导运营）。"""
        resp = auth_client.put(
            RULES_URL,
            {"rules": [{"code": "env_headless", "params": {"limit": 1}}]},
            format="json",
        )
        assert resp.status_code == 200
        assert CaptchaRule.objects.get(code="env_headless").params == {}
