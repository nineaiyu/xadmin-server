# -*- coding: utf-8 -*-
"""定时报表 cron 表达式集成测试：命中判定 + 校验 + 与三档互斥。"""

import pytest
from django.utils import timezone

from system.analysis_tasks import _cron_due, report_due
from system.models.dataset import Dataset, Report

pytestmark = pytest.mark.django_db

REPORTS_URL = "/api/system/reports"


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


class TestCronDue:
    def test_valid_expression_hits(self):
        now = timezone.localtime().replace(hour=10, minute=30, second=0, microsecond=0)
        assert _cron_due("30 10 * * *", now) is True
        assert _cron_due("*/15 * * * *", now) is True
        assert _cron_due("0 10 * * *", now) is False

    def test_invalid_expression_fail_closed(self):
        assert _cron_due("not a cron", timezone.localtime()) is False
        assert _cron_due("", timezone.localtime()) is False

    def test_report_due_prefers_cron(self, dataset):
        report = make_report(dataset, frequency="daily", send_time="08:00", cron_expression="0 9 * * *")
        at_nine = timezone.localtime().replace(hour=9, minute=0, second=0, microsecond=0)
        assert report_due(report, at_nine) is True
        at_eight = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        assert report_due(report, at_eight) is False

    def test_three_tier_unchanged_without_cron(self, dataset):
        report = make_report(dataset, frequency="daily", send_time="08:00")
        at_eight = timezone.localtime().replace(hour=8, minute=0, second=0, microsecond=0)
        assert report_due(report, at_eight) is True
        assert report_due(report, at_eight.replace(hour=9)) is False


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
