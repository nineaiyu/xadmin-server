# -*- coding: utf-8 -*-
"""账号安全巡检批量落库回归：新建 / 原地刷新 / 复现重开 / 自动解除。

巡检写入侧批量化（存量行一次取回 + bulk_create/bulk_update + 整批自动解除）后，
逐行处理时代的四条状态迁移语义必须逐条保持。
"""

import pytest

from identity.models import AccountRisk, UserInfo

pytestmark = pytest.mark.django_db


def _superuser_no_mfa_risk(user, **overrides):
    fields = {
        "user": user,
        "user_display": user.username,
        "risk_type": AccountRisk.RiskType.SUPERUSER_NO_MFA,
        "level": AccountRisk.Level.HIGH,
    }
    fields.update(overrides)
    return AccountRisk.objects.create(**fields)


class TestAccountRiskScanBatch:
    def test_scan_refreshes_pending_row_in_place(self, superuser):
        from identity.utils.account_risk import scan_account_risks

        risk = _superuser_no_mfa_risk(
            superuser,
            level=AccountRisk.Level.LOW,
            detail={"stale": True},
            user_display="legacy",
        )
        before = risk.updated_time

        stats = scan_account_risks()

        risk.refresh_from_db()
        assert stats["created"] == 0
        assert stats["updated"] >= 1
        assert risk.status == AccountRisk.Status.PENDING
        assert risk.level == AccountRisk.Level.HIGH
        assert risk.user_display == superuser.username
        assert risk.detail.get("description")
        assert risk.updated_time >= before

    def test_scan_reactivates_resolved_risk(self, superuser):
        """已处置（RESOLVED）的风险复现：重置 PENDING 并清空处置留痕。"""
        from django.utils import timezone

        from identity.utils.account_risk import scan_account_risks

        risk = _superuser_no_mfa_risk(
            superuser,
            status=AccountRisk.Status.RESOLVED,
            remark="manual",
            handled_by=superuser,
            handled_at=timezone.now(),
        )

        stats = scan_account_risks()

        risk.refresh_from_db()
        assert stats["updated"] >= 1
        assert risk.status == AccountRisk.Status.PENDING
        assert risk.remark == ""
        assert risk.handled_by_id is None
        assert risk.handled_at is None

    def test_scan_auto_resolves_disappeared_global_risk(self, superuser, settings):
        """风险消失的 PENDING 行整批自动解除：置 RESOLVED + 留自动解除备注。"""
        from identity.utils.account_risk import scan_account_risks

        settings.SECURITY_SUPERUSER_MAX_COUNT = 1
        risk = AccountRisk.objects.create(
            user=None,
            risk_type=AccountRisk.RiskType.SUPERUSER_COUNT,
            level=AccountRisk.Level.MEDIUM,
        )

        stats = scan_account_risks()

        risk.refresh_from_db()
        assert stats["resolved"] == 1
        assert risk.status == AccountRisk.Status.RESOLVED
        assert risk.handled_at is not None
        assert risk.remark

    def test_scan_keeps_ignored_and_mixed_statuses(self, superuser):
        """同一批扫描中 IGNORED 行不被翻动，其余行照常新建（批量写不越状态语义）。"""
        from identity.utils.account_risk import scan_account_risks

        ignored = _superuser_no_mfa_risk(superuser, status=AccountRisk.Status.IGNORED, remark="exempt")

        stats = scan_account_risks()

        ignored.refresh_from_db()
        assert stats["created"] == 0
        assert ignored.status == AccountRisk.Status.IGNORED
        assert ignored.remark == "exempt"

    def test_scan_counts_reflect_batch_writes(self, superuser):
        """返回计数与库内状态一一对应（created/updated/resolved 口径不变）。"""
        from identity.utils.account_risk import scan_account_risks

        fresh = UserInfo.objects.create_superuser(username="risk_count", password="x")
        first = scan_account_risks()
        assert first["created"] >= 1
        assert AccountRisk.objects.filter(user=fresh).exists()

        second = scan_account_risks()
        assert second["created"] == 0
        assert second["updated"] >= 1
        assert second["resolved"] == 0

    def test_scan_user_without_risk_row_is_created(self, superuser):
        from identity.utils.account_risk import scan_account_risks

        fresh = UserInfo.objects.create_superuser(username="risk_fresh", password="x")
        assert not AccountRisk.objects.filter(user=fresh).exists()

        stats = scan_account_risks()

        risk = AccountRisk.objects.filter(user=fresh, risk_type=AccountRisk.RiskType.SUPERUSER_NO_MFA).first()
        assert risk is not None
        assert stats["created"] >= 1
        assert risk.status == AccountRisk.Status.PENDING
