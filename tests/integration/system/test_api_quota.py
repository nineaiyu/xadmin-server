# -*- coding: utf-8 -*-
"""应用用量与配额（ADR-039 B3）：每日配额软告警 + 用量报表。

口径：
- 配额是软告警（不阻断请求）：达阈值当日首次越线发一次（webhook 事件 + 站内信超管）；
- 用量报表聚合 OperationLog 的 token_pk（应用全生命周期凭证），失败 = 业务码非成功。
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from system.models.log import OperationLog
from system.models.token import PersonalAccessToken
from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.utils.webhook import encrypt_secret

pytestmark = pytest.mark.django_db

APPS_URL = "/api/system/api-applications"
TOKEN_URL = "/api/system/open/token"
USER_URL = "/api/system/user"


def _create_application(client, **payload):
    data = {"name": "配额测试应用", "rate_limit_per_minute": 0}
    data.update(payload)
    resp = client.post(APPS_URL, data, format="json")
    assert resp.status_code == 201, resp.data
    return resp.data["data"]


def _issue_raw(application):
    resp = APIClient().post(
        TOKEN_URL,
        {"client_id": application["client_id"], "client_secret": application["client_secret"]},
        format="json",
    )
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["access_token"]


def _pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Pat {raw_token}")
    return client


class TestQuotaWarning:
    def test_threshold_crossing_emits_once_per_day(self, auth_client):
        """达阈值当日首次越线发一次 webhook 事件；软口径不阻断请求。"""
        WebhookSubscription.objects.create(
            name="配额告警订阅",
            url="http://127.0.0.1:9/hook",
            secret=encrypt_secret("whs-quota"),
            events=["api_quota.warning"],
        )
        application = _create_application(auth_client, daily_quota=4, quota_alert_percent=50)
        client = _pat_client(_issue_raw(application))
        for _ in range(5):
            assert client.get(USER_URL).status_code == 200  # 超阈值不阻断
        deliveries = WebhookDelivery.objects.filter(event="api_quota.warning")
        assert deliveries.count() == 1
        payload = deliveries.first().payload
        assert payload["data"]["client_id"] == application["client_id"]
        assert payload["data"]["quota"] == 4
        client.get(USER_URL)
        assert WebhookDelivery.objects.filter(event="api_quota.warning").count() == 1  # 当日不重复

    def test_no_quota_configured_no_warning(self, auth_client):
        WebhookSubscription.objects.create(
            name="配额告警订阅2",
            url="http://127.0.0.1:9/hook",
            secret=encrypt_secret("whs-quota"),
            events=["api_quota.warning"],
        )
        application = _create_application(auth_client)  # daily_quota 缺省 0 = 不限
        client = _pat_client(_issue_raw(application))
        for _ in range(3):
            client.get(USER_URL)
        assert WebhookDelivery.objects.filter(event="api_quota.warning").count() == 0

    def test_quota_fields_validation(self, auth_client):
        application = _create_application(auth_client)
        negative = auth_client.patch(f"{APPS_URL}/{application['pk']}", {"daily_quota": -1}, format="json")
        assert negative.status_code == 400
        out_of_range = auth_client.patch(f"{APPS_URL}/{application['pk']}", {"quota_alert_percent": 101}, format="json")
        assert out_of_range.status_code == 400


class TestUsageStats:
    def test_stats_aggregates_operation_logs(self, auth_client):
        application = _create_application(auth_client, daily_quota=100)
        token = _issue_raw(application)
        token_row = PersonalAccessToken.objects.get(api_application_id=application["pk"])
        now = timezone.now()
        for code, path, duration in (
            (1000, "/api/system/user", 0.1),
            (1000, "/api/system/user", 0.2),
            (1001, "/api/system/dept", 0.3),
        ):
            OperationLog.objects.create(
                module="system",
                path=path,
                method="GET",
                status_code=code,
                response_code=200,
                exec_time=duration,
                token_pk=token_row.pk,
                auth_type=OperationLog.AuthType.PAT,
                created_time=now,
            )
        # 一次真实认证请求进入当日配额计数
        assert _pat_client(token).get(USER_URL).status_code == 200

        resp = auth_client.get(f"{APPS_URL}/{application['pk']}/stats?days=7")
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["total"] == 3
        assert data["failed"] == 1
        assert data["avg_duration"] == pytest.approx(0.2, abs=1e-6)
        assert data["daily"][0]["total"] == 3
        assert data["top_paths"][0]["path"] == "/api/system/user"
        assert data["quota"]["daily_quota"] == 100
        assert data["quota"]["used_today"] == 1

    def test_stats_scoped_to_application_tokens(self, auth_client):
        """其他应用的日志不进入本应用报表。"""
        application = _create_application(auth_client)
        other = _create_application(auth_client)
        other_token = PersonalAccessToken.objects.create(
            name="app:other",
            token_hash="x" * 64,
            token_prefix="other",
            api_application_id=other["pk"],
        )
        OperationLog.objects.create(
            module="system",
            path="/api/system/user",
            method="GET",
            status_code=1000,
            token_pk=other_token.pk,
            created_time=timezone.now(),
        )
        resp = auth_client.get(f"{APPS_URL}/{application['pk']}/stats")
        assert resp.data["data"]["total"] == 0

    def test_stats_days_clamped(self, auth_client):
        application = _create_application(auth_client)
        resp = auth_client.get(f"{APPS_URL}/{application['pk']}/stats?days=999")
        assert resp.data["data"]["days"] == 30
