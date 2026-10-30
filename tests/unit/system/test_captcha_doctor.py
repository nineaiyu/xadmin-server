# -*- coding: utf-8 -*-
"""验证码风险档自检口径：反代信任、网段兜底与影子期样本。"""

from system.utils.platform.captcha_doctor import captcha_risk_findings


def _names(findings):
    return [item["name"] for item in findings]


def test_off_mode_is_informational_only():
    findings = captcha_risk_findings(
        mode="off",
        whitelist=[],
        trust_private=True,
        trusted_proxies=[],
    )
    assert [item["level"] for item in findings] == ["pass"]


def test_enforce_without_trusted_proxies_warns():
    findings = captcha_risk_findings(
        mode="enforce",
        whitelist=["10.0.0.0/8"],
        trust_private=True,
        trusted_proxies=[],
        samples=5000,
        days=7,
    )
    assert "验证码反代" in _names(findings)
    assert any(item["level"] == "warn" for item in findings)


def test_enforce_without_trust_network_returns_single_warning():
    findings = captcha_risk_findings(
        mode="enforce",
        whitelist=[],
        trust_private=False,
        trusted_proxies=["172.18.0.0/16"],
        samples=5000,
        days=7,
    )
    assert [item["name"] for item in findings] == ["验证码风险档"]
    assert findings[0]["level"] == "warn"


def test_enforce_with_insufficient_shadow_samples_warns():
    findings = captcha_risk_findings(
        mode="enforce",
        whitelist=["10.0.0.0/8"],
        trust_private=True,
        trusted_proxies=["172.18.0.0/16"],
        samples=10,
        days=1,
    )
    assert _names(findings) == ["验证码影子期"]


def test_enforce_with_ready_shadow_baseline_passes():
    findings = captcha_risk_findings(
        mode="enforce",
        whitelist=["10.0.0.0/8"],
        trust_private=True,
        trusted_proxies=["172.18.0.0/16"],
        samples=5000,
        days=7,
    )
    assert [item["level"] for item in findings] == ["pass"]
