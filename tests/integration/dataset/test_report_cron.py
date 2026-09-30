# -*- coding: utf-8 -*-
"""定时报表 cron 表达式集成测试：命中判定 + 校验 + 与三档互斥。"""

import datetime

import pytest
from django.utils import timezone

from dataset.analysis_tasks import report_due
from dataset.models.dataset import Dataset, Report

pytestmark = pytest.mark.django_db

REPORTS_URL = "/api/dataset/reports"


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(name="cron_ds", bound_model="system.userinfo", visibility="personal")


def make_report(dataset, **kwargs):
    defaults = {
        "name": kwargs.pop("name", f"报表-{timezone.now().timestamp()}"),
        "dataset": dataset,
        "recipients": ["a@example.com"],
        "is_active": True,
    }
    defaults.update(kwargs)
    return Report.objects.create(**defaults)


class TestReportDue:
    """到期判定：以「最近一次应当执行的时刻」为基准（含漏跑补偿）。"""

    def test_cron_report_due_after_hit(self, dataset):
        report = make_report(dataset, frequency="daily", send_time="08:00", cron_expression="0 9 * * *")
        now = timezone.localtime().replace(second=0, microsecond=0)
        yesterday_nine = now.replace(hour=9, minute=0) - datetime.timedelta(days=1)
        Report.objects.filter(pk=report.pk).update(last_run_at=yesterday_nine)
        report.refresh_from_db()

        # 命中时刻（09:00）之后任意时刻都算到期（分发器 :05 扫描 / 补跑）
        at_nine_thirty = now.replace(hour=9, minute=30)
        assert report_due(report, at_nine_thirty) is True

        # 已经跑过最近一次命中 → 不重复派发
        Report.objects.filter(pk=report.pk).update(last_run_at=now.replace(hour=9, minute=0))
        report.refresh_from_db()
        assert report_due(report, at_nine_thirty) is False

    def test_cron_report_invalid_expression_fail_closed(self, dataset):
        report = make_report(dataset, cron_expression="not a cron")
        assert report_due(report, timezone.localtime()) is False

    def test_daily_report_catches_up_missed_minute(self, dataset):
        """漏跑补偿：命中 08:00 但当时进程不可用，10:20 的扫描必须补发。"""
        report = make_report(dataset, frequency="daily", send_time="08:00")
        now = timezone.localtime().replace(second=0, microsecond=0)
        Report.objects.filter(pk=report.pk).update(
            last_run_at=now.replace(hour=8, minute=0) - datetime.timedelta(days=1)
        )
        report.refresh_from_db()
        assert report_due(report, now.replace(hour=10, minute=20)) is True

    def test_daily_report_not_due_after_today_run(self, dataset):
        report = make_report(dataset, frequency="daily", send_time="08:00")
        now = timezone.localtime().replace(second=0, microsecond=0)
        Report.objects.filter(pk=report.pk).update(last_run_at=now.replace(hour=8, minute=0))
        report.refresh_from_db()
        assert report_due(report, now.replace(hour=10, minute=20)) is False

    def test_new_report_does_not_backfill_before_creation(self, dataset):
        """新建报表不补发建单之前的期次（以建单时刻为参考点）。"""
        report = make_report(dataset, frequency="daily", send_time="08:00")
        now = timezone.localtime().replace(second=0, microsecond=0)
        Report.objects.filter(pk=report.pk).update(created_time=now.replace(hour=9, minute=0))
        report.refresh_from_db()
        assert report_due(report, now.replace(hour=10, minute=20)) is False
        # 明天的 08:00 到期
        tomorrow = now.replace(hour=10, minute=20) + datetime.timedelta(days=1)
        assert report_due(report, tomorrow) is True

    def test_send_time_minute_no_longer_must_match_exactly(self, dataset):
        """原实现要求当前分钟精确等于 send_time 的分钟（分发器 :05 扫描 → 多数配置永不触发）。"""
        report = make_report(dataset, frequency="daily", send_time="09:30")
        now = timezone.localtime().replace(second=0, microsecond=0)
        Report.objects.filter(pk=report.pk).update(
            last_run_at=now.replace(hour=9, minute=30) - datetime.timedelta(days=1)
        )
        report.refresh_from_db()
        assert report_due(report, now.replace(hour=9, minute=35)) is True

    def test_weekly_and_monthly_due_moments(self, dataset):
        report = make_report(dataset, frequency="weekly", send_time="08:00", weekday=timezone.localtime().weekday())
        now = timezone.localtime().replace(second=0, microsecond=0)
        Report.objects.filter(pk=report.pk).update(
            last_run_at=now.replace(hour=8, minute=0) - datetime.timedelta(days=7)
        )
        report.refresh_from_db()
        assert report_due(report, now.replace(hour=12)) is True

        monthly = make_report(dataset, name="月报", frequency="monthly", send_time="08:00")
        Report.objects.filter(pk=monthly.pk).update(
            created_time=now.replace(day=1, hour=8, minute=0) - datetime.timedelta(days=1)
        )
        monthly.refresh_from_db()
        # 用「本月 1 号 09:00」作判定时刻（显式 dt）：直接传 now 会在每月 1 日
        # 00:00~08:00 窗口内取到「上月到期点 < 建单时间」而假失败（与真实时钟耦合）
        assert report_due(monthly, now.replace(day=1, hour=9)) is True

    def test_invalid_send_time_fail_closed(self, dataset):
        report = make_report(dataset, frequency="daily", send_time="25:99")
        assert report_due(report, timezone.localtime()) is False


class TestCronValidation:
    def test_create_with_valid_cron(self, auth_client, dataset):
        resp = auth_client.post(
            REPORTS_URL,
            {
                "name": f"cron报表-{timezone.now().timestamp()}",
                "dataset": str(dataset.pk),
                "frequency": "daily",
                "send_time": "08:00",
                "cron_expression": "0 9 * * 1-5",
                "recipients": ["ops@example.com"],
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_invalid_cron_rejected(self, auth_client, dataset):
        resp = auth_client.post(
            REPORTS_URL,
            {
                "name": f"badcron-{timezone.now().timestamp()}",
                "dataset": str(dataset.pk),
                "frequency": "daily",
                "send_time": "08:00",
                "cron_expression": "99 99 * * *",
                "recipients": ["ops@example.com"],
            },
            format="json",
        )
        assert resp.status_code == 400 or resp.data.get("code") != 1000, resp.data
