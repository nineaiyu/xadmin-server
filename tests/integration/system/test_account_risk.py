# -*- coding: utf-8 -*-
"""账号安全风险巡检（F-6）集成测试。

口径钉死：
- 巡检幂等（同一用户同一风险类型一行），风险消失自动置 RESOLVED；
- 人工 IGNORED（豁免）不被巡检翻回 PENDING；
- 处置动作留痕（handled_by / handled_at）；强制改密经 record_password_hash 清除标记。
"""

import pytest

from system.models import AccountRisk, UserInfo

pytestmark = pytest.mark.django_db

LIST_URL = "/api/system/account-risks"
SCAN_URL = "/api/system/account-risks/scan"
STATS_URL = "/api/system/account-risks/stats"


def _handle_url(pk):
    return f"/api/system/account-risks/{pk}/handle"


def _risk_of(user, risk_type):
    return AccountRisk.objects.filter(user=user, risk_type=risk_type).first()


class TestAccountRiskScan:
    def test_scan_creates_superuser_no_mfa(self, superuser):
        from system.utils.account_risk import scan_account_risks

        stats = scan_account_risks()
        assert stats["created"] >= 1
        risk = _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA)
        assert risk is not None
        assert risk.level == AccountRisk.Level.HIGH
        assert risk.status == AccountRisk.Status.PENDING

    def test_scan_is_idempotent(self, superuser):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        total = AccountRisk.objects.count()
        stats = scan_account_risks()
        assert stats["created"] == 0
        assert AccountRisk.objects.count() == total

    def test_risk_auto_resolved_when_disappeared(self, superuser, settings):
        from system.utils.account_risk import scan_account_risks

        settings.SECURITY_SUPERUSER_MAX_COUNT = 0
        scan_account_risks()
        assert _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA) is not None
        # 绑定 OTP → 管理员未绑 MFA 风险消失
        superuser.otp_secret_key = "JBSWY3DPEHPK3PXP"
        superuser.mfa_level = UserInfo.MFALevelChoices.ENABLED
        superuser.save(update_fields=["otp_secret_key", "mfa_level"])
        scan_account_risks()
        risk = _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA)
        assert risk.status == AccountRisk.Status.RESOLVED

    def test_ignored_risk_kept_by_next_scan(self, superuser, auth_client):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        risk = _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA)
        resp = auth_client.post(_handle_url(risk.pk), {"action": "ignore"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        risk.refresh_from_db()
        assert risk.status == AccountRisk.Status.IGNORED
        assert risk.handled_by_id == superuser.pk
        assert risk.handled_at is not None
        scan_account_risks()
        risk.refresh_from_db()
        # 豁免不被打扰（巡检不翻回 PENDING）
        assert risk.status == AccountRisk.Status.IGNORED

    def test_unsupported_handle_action(self, superuser, auth_client):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        risk = _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA)
        resp = auth_client.post(_handle_url(risk.pk), {"action": "unknown"}, format="json")
        assert resp.data["code"] == 1001


class TestAccountRiskDispose:
    def test_force_change_password_then_login_flag(self, superuser, auth_client, settings):
        from settings.services import record_password_hash
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        risk = _risk_of(superuser, AccountRisk.RiskType.SUPERUSER_NO_MFA)
        resp = auth_client.post(_handle_url(risk.pk), {"action": "force_change_password"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        superuser.refresh_from_db()
        assert superuser.must_change_password is True
        # 改密（任意链路都经 record_password_hash）清除标记
        record_password_hash(superuser, "pbkdf2_sha256$fake")
        superuser.refresh_from_db()
        assert superuser.must_change_password is False

    def test_userinfo_exposes_must_change_password(self, auth_client, superuser):
        """F-6 引导改密：强制改密标记随 userinfo 下发（刷新页面后仍可引导）"""
        superuser.must_change_password = True
        superuser.save(update_fields=["must_change_password"])
        resp = auth_client.get("/api/system/userinfo")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["must_change_password"] is True

    def test_disable_user_action(self, normal_user, superuser, auth_client):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        risk = AccountRisk.objects.create(
            user=normal_user,
            user_display=normal_user.nickname,
            risk_type=AccountRisk.RiskType.LOGIN_STALE,
            level=AccountRisk.Level.LOW,
        )
        resp = auth_client.post(_handle_url(risk.pk), {"action": "disable"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        normal_user.refresh_from_db()
        assert normal_user.is_active is False

    def test_batch_handle_returns_detail(self, superuser, normal_user, auth_client):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        risks = list(AccountRisk.objects.filter(status=AccountRisk.Status.PENDING)[:2])
        resp = auth_client.post(
            "/api/system/account-risks/batch-handle",
            {"pks": [str(risk.pk) for risk in risks], "action": "notify"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert len(resp.data["data"]["success"]) == len(risks)

    def test_scan_and_stats_endpoints(self, auth_client, superuser):
        resp = auth_client.post(SCAN_URL, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert "total" in resp.data["data"]
        resp = auth_client.get(STATS_URL)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["pending"] >= 1
        assert "ignore" in resp.data["data"]["actions"]

    def test_list_endpoint_filters(self, auth_client, superuser):
        from system.utils.account_risk import scan_account_risks

        scan_account_risks()
        resp = auth_client.get(LIST_URL, {"risk_type": "superuser_no_mfa"})
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["total"] >= 1
