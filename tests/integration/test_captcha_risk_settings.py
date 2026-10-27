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


class TestRiskRules:
    def test_rules_are_seeded(self, auth_client):
        resp = auth_client.get(RULES_URL)
        assert resp.status_code == 200, resp.data
        rules = resp.data["data"]
        assert len(rules) == 16
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

    def test_unknown_rule_is_noop(self, auth_client):
        resp = auth_client.put(RULES_URL, {"rules": [{"code": "no_such_rule", "score": 10}]}, format="json")
        assert resp.status_code == 200
        assert not CaptchaRule.objects.filter(code="no_such_rule").exists()
