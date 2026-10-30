# -*- coding: utf-8 -*-
"""Webhook 通知渠道设置面：读写契约与连通性测试。

- retrieve 回显三项配置且 secret 不回显（write_only 值级加密）；
- partialUpdate 持久化到 ``Setting``（category=notify_webhook）；
- create（测试）按生效配置快照走真实出站链路——mock ``pinned_request`` 分别
  断言：未启用 / 缺 URL / 出站成功（2xx）/ 端点非 2xx / 出站守卫拒绝。
"""

import json
from unittest.mock import patch

import pytest

from settings.models import Setting

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/settings/notify/webhook"


def _ok_response(status_code=200):
    class Resp:
        pass

    resp = Resp()
    resp.status_code = status_code
    return resp


class TestWebhookSettingCrud:
    def test_retrieve_returns_fields_without_secret(self, auth_client):
        resp = auth_client.get(WEBHOOK_URL)
        assert resp.status_code == 200
        data = resp.data["data"]
        assert set(data) == {"WEBHOOK_ENABLED", "WEBHOOK_URL"}

    def test_partial_update_persists(self, auth_client):
        resp = auth_client.patch(
            WEBHOOK_URL,
            {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": "https://ops.example.com/hook", "WEBHOOK_SECRET": "s3cret"},
        )
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        enabled = Setting.objects.filter(name="WEBHOOK_ENABLED", category="notify_webhook").first()
        url = Setting.objects.filter(name="WEBHOOK_URL", category="notify_webhook").first()
        secret = Setting.objects.filter(name="WEBHOOK_SECRET", category="notify_webhook").first()
        assert enabled is not None and enabled.cleaned_value is True
        assert url is not None and url.cleaned_value == "https://ops.example.com/hook"
        assert secret is not None and secret.encrypted is True

    def test_url_required(self, auth_client):
        resp = auth_client.patch(WEBHOOK_URL, {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": ""})
        assert resp.status_code == 400


class TestWebhookConnectivity:
    def _persist(self, enabled=True, url="https://ops.example.com/hook"):
        """测试链路的配置落库口径：value 按存储格式 JSON 序列化（ViewSet 写入同款）。"""
        Setting.objects.update_or_create(
            name="WEBHOOK_ENABLED", defaults={"category": "notify_webhook", "value": json.dumps(enabled)}
        )
        Setting.objects.update_or_create(
            name="WEBHOOK_URL", defaults={"category": "notify_webhook", "value": json.dumps(url)}
        )
        Setting.objects.get(name="WEBHOOK_ENABLED").refresh_setting()
        Setting.objects.get(name="WEBHOOK_URL").refresh_setting()

    def test_disabled_channel_reports_disabled(self, auth_client):
        """表单完整但开关关闭：给出 Disabled 而非空跑出站（与 IM 渠道测试口径一致）。"""
        resp = auth_client.post(WEBHOOK_URL, {"WEBHOOK_ENABLED": False, "WEBHOOK_URL": "https://ops.example.com/hook"})
        assert resp.data["code"] == 1002

    def test_delivery_success(self, auth_client):
        self._persist()
        with patch("common.utils.outbound.pinned_request", return_value=_ok_response(204)) as deliver:
            resp = auth_client.post(
                WEBHOOK_URL, {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": "https://ops.example.com/hook"}
            )
        assert resp.data["code"] == 1000
        deliver.assert_called_once()

    def test_endpoint_non_2xx_reports_failure(self, auth_client):
        self._persist()
        with patch("common.utils.outbound.pinned_request", return_value=_ok_response(502)):
            resp = auth_client.post(
                WEBHOOK_URL, {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": "https://ops.example.com/hook"}
            )
        assert resp.data["code"] == 1002
        assert "502" in str(resp.data["detail"])

    def test_outbound_guard_rejection_is_readable(self, auth_client):
        from common.utils.outbound import OutboundBlocked

        self._persist()
        blocked = OutboundBlocked("private address")
        with patch("common.utils.outbound.pinned_request", side_effect=blocked):
            resp = auth_client.post(
                WEBHOOK_URL, {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": "https://ops.example.com/hook"}
            )
        assert resp.data["code"] == 1002
