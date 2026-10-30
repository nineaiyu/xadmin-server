# -*- coding: utf-8 -*-
"""风险引擎：22 条规则 / 阈值边界 / 信号提取 / 信任网段。"""

import pytest

from captcha import risk, signals


def _hits(**overrides):
    base = {code: False for code in risk.DEFAULT_RULE_CODES}
    base.update(overrides)
    return base


class TestDecisionThresholds:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, "allow"),
            (19, "allow"),
            (20, "light"),
            (39, "light"),
            (40, "challenge"),
            (69, "challenge"),
            (70, "hard"),
            (89, "hard"),
            (90, "deny"),
            (100, "deny"),
        ],
    )
    def test_boundaries(self, score, expected):
        assert risk.decide(score) == expected

    def test_thresholds_are_configurable(self, settings):
        settings.CAPTCHA_RISK_T_ALLOW = 10
        settings.CAPTCHA_RISK_T_LIGHT = 20
        settings.CAPTCHA_RISK_T_HARD = 30
        settings.CAPTCHA_RISK_T_DENY = 40
        assert risk.decide(15) == "light"
        assert risk.decide(25) == "challenge"
        assert risk.decide(35) == "hard"
        assert risk.decide(45) == "deny"


class TestRuleEvaluation:
    @pytest.mark.parametrize(("code", "score"), [(rule["code"], rule["score"]) for rule in risk.DEFAULT_RULES])
    def test_each_rule_contributes_its_score(self, code, score):
        result = risk.evaluate(_hits(**{code: True}))
        if code == "honeypot":
            assert result.decision == "deny"
            assert result.score == 100
        elif code == "hist_good":
            assert result.score == 0  # 负分后夹紧到 0
        else:
            assert result.score == score
            assert code in result.reasons

    def test_disabled_rule_ignored(self):
        rules = [{"code": "env_headless", "scope": "", "score": 60, "priority": 1, "params": {}}]
        assert risk.evaluate(_hits(env_headless=True), rules=rules).score == 60
        assert risk.evaluate(_hits(env_missing=True), rules=rules).score == 0

    def test_scope_filter(self):
        rules = [{"code": "env_missing", "scope": "invite", "score": 40, "priority": 1, "params": {}}]
        assert risk.evaluate(_hits(env_missing=True), rules=rules, scene="login").score == 0
        assert risk.evaluate(_hits(env_missing=True), rules=rules, scene="invite").score == 40

    def test_honeypot_short_circuits(self):
        result = risk.evaluate(_hits(honeypot=True))
        assert result.decision == "deny"
        assert result.reasons == ["honeypot"]

    def test_score_clamped_to_100(self):
        hits = _hits(env_headless=True, env_missing=True, ua_mismatch=True, net_proxy=True)
        assert risk.evaluate(hits).score == 100


class TestSignals:
    def test_env_signals(self):
        hits = signals.env_signals({"v": 0}, ua="HeadlessChrome/126", accept_language="en-US")
        assert hits["env_missing"] is True
        assert hits["env_headless"] is True

    def test_env_contradiction(self):
        env = {"v": 1, "timezone": "Not/AZone", "language": "zh-CN", "screen": {"width": 0, "height": 0, "dpr": 1}}
        assert signals.env_signals(env, accept_language="zh-CN")["env_contradiction"] is True
        env_ok = {
            "v": 1,
            "timezone": "Asia/Shanghai",
            "language": "zh-CN",
            "screen": {"width": 1920, "height": 1080, "dpr": 1},
        }
        assert signals.env_signals(env_ok, accept_language="zh-CN")["env_contradiction"] is False

    def test_ua_mismatch(self):
        assert signals.env_signals({"v": 1}, ua="Mozilla/5.0 Chrome/126.0", sec_ch_ua="")["ua_mismatch"] is True
        assert (
            signals.env_signals({"v": 1}, ua="Mozilla/5.0 Chrome/126.0", sec_ch_ua='"Chromium";v="126"')["ua_mismatch"]
            is False
        )

    def test_track_metrics_and_keyboard(self):
        straight = [{"x": 0, "y": 0, "t": 0}, {"x": 50, "y": 0, "t": 50}, {"x": 100, "y": 0, "t": 100}]
        metrics = signals.track_metrics(straight)
        assert metrics["straightness"] == 1.0
        assert signals.track_signals(metrics)["track_straight"] is True
        assert signals.track_signals(metrics)["track_toofast"] is True
        # 键盘操作永不因轨迹像脚本被扣分
        keyboard = signals.track_signals({**metrics, "keyboard": True})
        assert not any(keyboard.values())

    def test_track_uniform_variance(self):
        uniform = [{"x": 0, "y": 0, "t": 0}, {"x": 10, "y": 0, "t": 100}, {"x": 20, "y": 0, "t": 200}]
        assert signals.track_signals(signals.track_metrics(uniform))["track_uniform"] is True
        varied = [{"x": 0, "y": 0, "t": 0}, {"x": 100, "y": 0, "t": 10}, {"x": 110, "y": 0, "t": 1000}]
        assert signals.track_signals(signals.track_metrics(varied))["track_uniform"] is False

    def test_xff_consistency(self, settings):
        meta = {"REMOTE_ADDR": "8.8.8.8", "HTTP_X_FORWARDED_FOR": "1.1.1.1"}
        settings.TRUSTED_PROXY_IPS = []
        assert signals.xff_inconsistent(meta) is False  # 未配置可信代理 = 不判定
        settings.TRUSTED_PROXY_IPS = ["10.0.0.1/32"]
        assert signals.xff_inconsistent(meta) is True  # 直连不可信却带 XFF
        trusted = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "8.8.8.8"}
        assert signals.xff_inconsistent(trusted) is False
        assert signals.xff_inconsistent({**trusted, "HTTP_X_FORWARDED_FOR": "8.8.8.8, bad"}) is True

    def test_xff_maps_to_net_proxy(self):
        hits = risk.collect_hits(xff_inconsistent=True, count_freq=False)
        assert hits["net_proxy"] is True

    def test_private_and_cidrs(self):
        assert signals.is_private_ip("10.0.0.8") is True
        assert signals.is_private_ip("8.8.8.8") is False
        assert signals.in_cidrs("8.8.8.8", "8.8.8.0/24") is True
        assert signals.in_cidrs("9.9.9.9", "8.8.8.0/24") is False

    def test_hash_ip_only_keeps_network_prefix(self):
        assert signals.hash_ip("10.1.2.3") == signals.hash_ip("10.1.2.99")
        assert signals.hash_ip("10.1.2.3") != signals.hash_ip("10.1.3.3")


class TestTrust:
    def test_private_network_trusted_when_proxy_configured(self, settings):
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = True
        settings.TRUSTED_PROXY_IPS = ["10.0.0.1/32"]
        assert risk.is_trusted_ip("127.0.0.1") is True
        result = risk.assess(ip="10.0.0.5", env={"webdriver": True})
        assert result.trusted is True
        assert result.decision == "allow"

    def test_private_network_not_trusted_without_proxy(self, settings):
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = True
        settings.TRUSTED_PROXY_IPS = []
        assert risk.is_trusted_ip("127.0.0.1") is False

    def test_public_ip_not_trusted(self, settings):
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = False
        settings.SECURITY_LOGIN_IP_WHITE_LIST = []
        assert risk.is_trusted_ip("8.8.8.8") is False

    def test_whitelist_hit(self, settings):
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = False
        settings.SECURITY_LOGIN_IP_WHITE_LIST = ["8.8.8.0/24"]
        assert risk.is_trusted_ip("8.8.8.8") is True

    def test_stricter_merges_two_assessments(self):
        light = risk.RiskResult(score=25, reasons=["env_contradiction"], decision="light")
        hard = risk.RiskResult(score=75, reasons=["env_headless"], decision="hard")
        assert risk.stricter(light, hard) is hard
        assert risk.stricter(hard, light) is hard
