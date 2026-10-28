# -*- coding: utf-8 -*-
"""设置项跨字段组合约束测试（SET-P1-02 / G17）。

锁定 validate() 组合校验：验证码字符集至少一项、登录方式/注册与找回密码的
验证码发送渠道至少一项、验证码分档上界包含（PoW 难度 ≤ MAX 封顶）、风险
阈值单调在 PATCH 下按「保存后实际生效值」判定（未提交键回落运行时值）。
"""

import pytest

pytestmark = pytest.mark.django_db

VERIFY_URL = "/api/settings/verify"
LOGIN_AUTH_URL = "/api/settings/login/auth"
REGISTER_AUTH_URL = "/api/settings/register/auth"
RESET_AUTH_URL = "/api/settings/reset/auth"
CAPTCHA_RISK_URL = "/api/settings/captcha/risk"


class TestVerifyCodeCharsetCombo:
    def test_all_three_charsets_off_rejected(self, auth_client):
        """字符集三项全关：保存即 400，而不是等发码链路 500。"""
        resp = auth_client.patch(
            VERIFY_URL,
            {"VERIFY_CODE_UPPER_CASE": False, "VERIFY_CODE_LOWER_CASE": False, "VERIFY_CODE_DIGIT_CASE": False},
        )
        assert resp.status_code == 400

    def test_turning_off_last_enabled_charset_rejected(self, auth_client, settings):
        """PATCH 单关最后一项（未提交键回落运行时值）同样拦截。"""
        settings.VERIFY_CODE_UPPER_CASE = False
        settings.VERIFY_CODE_LOWER_CASE = False
        settings.VERIFY_CODE_DIGIT_CASE = True
        resp = auth_client.patch(VERIFY_URL, {"VERIFY_CODE_DIGIT_CASE": False})
        assert resp.status_code == 400

    def test_at_least_one_enabled_passes(self, auth_client):
        resp = auth_client.patch(VERIFY_URL, {"VERIFY_CODE_UPPER_CASE": True})
        assert resp.data["code"] == 1000, resp.data


class TestScenarioChannelCombo:
    def test_register_enabled_without_channel_rejected(self, auth_client):
        resp = auth_client.patch(
            REGISTER_AUTH_URL,
            {
                "SECURITY_REGISTER_ACCESS_ENABLED": True,
                "SECURITY_REGISTER_BY_EMAIL_ENABLED": False,
                "SECURITY_REGISTER_BY_SMS_ENABLED": False,
            },
        )
        assert resp.status_code == 400

    def test_register_disabled_allows_channels_off(self, auth_client):
        """场景整体关闭时允许通道全关（停用注册是合法状态）。"""
        resp = auth_client.patch(
            REGISTER_AUTH_URL,
            {
                "SECURITY_REGISTER_ACCESS_ENABLED": False,
                "SECURITY_REGISTER_BY_EMAIL_ENABLED": False,
                "SECURITY_REGISTER_BY_SMS_ENABLED": False,
            },
        )
        assert resp.data["code"] == 1000, resp.data

    def test_reset_enabled_without_channel_rejected(self, auth_client):
        resp = auth_client.patch(
            RESET_AUTH_URL,
            {
                "SECURITY_RESET_PASSWORD_ACCESS_ENABLED": True,
                "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED": False,
                "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED": False,
            },
        )
        assert resp.status_code == 400

    def test_reset_with_email_channel_passes(self, auth_client):
        resp = auth_client.patch(
            RESET_AUTH_URL,
            {
                "SECURITY_RESET_PASSWORD_ACCESS_ENABLED": True,
                "SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED": True,
                "SECURITY_RESET_PASSWORD_BY_SMS_ENABLED": False,
            },
        )
        assert resp.data["code"] == 1000, resp.data

    def test_login_enabled_without_any_method_rejected(self, auth_client):
        resp = auth_client.patch(
            LOGIN_AUTH_URL,
            {
                "SECURITY_LOGIN_ACCESS_ENABLED": True,
                "SECURITY_LOGIN_BY_BASIC_ENABLED": False,
                "SECURITY_LOGIN_BY_EMAIL_ENABLED": False,
                "SECURITY_LOGIN_BY_SMS_ENABLED": False,
            },
        )
        assert resp.status_code == 400

    def test_login_with_basic_only_passes(self, auth_client):
        resp = auth_client.patch(
            LOGIN_AUTH_URL,
            {
                "SECURITY_LOGIN_ACCESS_ENABLED": True,
                "SECURITY_LOGIN_BY_BASIC_ENABLED": True,
                "SECURITY_LOGIN_BY_EMAIL_ENABLED": False,
                "SECURITY_LOGIN_BY_SMS_ENABLED": False,
            },
        )
        assert resp.data["code"] == 1000, resp.data


class TestCaptchaRiskCombo:
    def test_pow_bits_above_cap_rejected(self, auth_client, settings):
        """分档上界包含：难度超过 MAX 封顶在运行期会被静默下调，保存期拒绝。"""
        settings.CAPTCHA_POW_BITS_HARD = 20
        settings.CAPTCHA_POW_BITS_LIGHT = 18
        settings.CAPTCHA_POW_BITS_ALLOW = 16
        resp = auth_client.patch(CAPTCHA_RISK_URL, {"CAPTCHA_POW_BITS_MAX": 15})
        assert resp.status_code == 400

    def test_pow_bits_within_cap_passes(self, auth_client):
        resp = auth_client.patch(CAPTCHA_RISK_URL, {"CAPTCHA_POW_BITS_MAX": 24})
        assert resp.data["code"] == 1000, resp.data

    def test_threshold_monotonic_uses_runtime_values(self, auth_client, settings):
        """阈值单调按「保存后生效值」判定：未提交键取运行时值，不被字段默认值污染。"""
        settings.CAPTCHA_RISK_T_ALLOW = 20
        settings.CAPTCHA_RISK_T_LIGHT = 30
        settings.CAPTCHA_RISK_T_HARD = 60
        settings.CAPTCHA_RISK_T_DENY = 90
        # 生效值 [40, 30, 60, 90] 不单调 → 拒绝
        resp = auth_client.patch(CAPTCHA_RISK_URL, {"CAPTCHA_RISK_T_LIGHT": 10})
        assert resp.status_code == 400
        # 生效值 [40, 50, 60, 90] 严格递增 → 放行（旧的 attrs.get 口径会拿默认值 40
        # 参与 [40,40] 重复判定而误拒）
        resp = auth_client.patch(CAPTCHA_RISK_URL, {"CAPTCHA_RISK_T_LIGHT": 50})
        assert resp.data["code"] == 1000, resp.data
