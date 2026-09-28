# -*- coding: utf-8 -*-
"""凭据与密钥 API：只读聚合 / 轮换语义 / 白名单 / 权限门控 / 值不外泄。"""

import json

import pytest
from rest_framework.test import APIClient

from system.models import SystemConfig

pytestmark = pytest.mark.django_db

URL = "/api/system/credentials"


class TestCredentialOverview:
    def test_requires_auth(self, api_client):
        assert api_client.get(f"{URL}/overview").status_code == 401

    def test_lists_registry_keys(self, auth_client):
        body = auth_client.get(f"{URL}/overview").json()
        assert body["code"] == 1000
        names = [row["name"] for row in body["data"]["system_configs"]]
        assert "OAUTH_PROVIDERS" in names and "SCIM_TOKEN" in names
        assert body["data"]["plaintext"] == []
        # 模型字段级凭据也纳入聚合（不返回值）
        scopes = {row["scope"] for row in body["data"]["model_fields"]}
        assert scopes == {"model_field"}

    def test_marks_actionable_semantics(self, auth_client):
        """每条凭据必须表达其可运维动作：自生成可轮换、外部签发只给更换入口。"""
        data = auth_client.get(f"{URL}/overview").json()["data"]
        configs = {row["name"]: row for row in data["system_configs"]}
        assert configs["SCIM_TOKEN"]["rotatable"] is True
        assert configs["SCIM_TOKEN"]["change_entry"] == ""
        assert configs["OAUTH_PROVIDERS"]["rotatable"] is False
        assert configs["OAUTH_PROVIDERS"]["change_entry"] == "/system/config/system/index"
        fields = {row["name"]: row for row in data["model_fields"]}
        assert fields["WebhookSubscription.secret"]["rotatable"] is True
        assert fields["AiProfile.api_key"]["rotatable"] is False
        assert fields["AiProfile.api_key"]["change_entry"] == "/integration/ai/config"
        # 掩码只表达「已配置」，不含明文/长度
        assert configs["SCIM_TOKEN"]["masked"] in ("", "••••••")

    def test_never_leaks_values(self, auth_client):
        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        body = auth_client.get(f"{URL}/overview").json()["data"]
        assert "plain-token" not in json.dumps(body, ensure_ascii=False)
        assert "SCIM_TOKEN" in body["plaintext"]

    def test_includes_setting_plaintext_risk(self, auth_client):
        """Setting 侧明文风险纳入告警面（复用巡检判定，与 CLI 同源）。"""
        from settings.models import Setting

        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        Setting.objects.create(name="EMAIL_HOST_PASSWORD", value="plain-pwd", category="email", encrypted=False)
        body = auth_client.get(f"{URL}/overview").json()["data"]
        assert "SCIM_TOKEN" in body["plaintext"]
        assert "Setting:EMAIL_HOST_PASSWORD" in body["plaintext"]
        assert "plain-pwd" not in json.dumps(body, ensure_ascii=False)

    def test_unprivileged_user_forbidden(self, normal_user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(f"{URL}/overview").status_code == 403


class TestCredentialRotate:
    def test_rotate_regenerates_and_encrypts(self, auth_client):
        """系统自生成键：轮换 = 重新生成随机值并加密落库（不是重加密同一值）。"""
        from common.core.credentials import decrypt_setting_value

        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        body = auth_client.post(f"{URL}/rotate", {"key": "SCIM_TOKEN"}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["action"] == "rotate"
        value = SystemConfig.objects.get(key="SCIM_TOKEN").value
        assert value.startswith("v3:")
        assert decrypt_setting_value("SCIM_TOKEN", value) != "plain-token"

    def test_rotate_rejects_replace_only_key(self, auth_client):
        """外部签发键拒绝原地轮换（避免生成对端不认的假值）。"""
        body = auth_client.post(f"{URL}/rotate", {"key": "OAUTH_PROVIDERS"}, format="json").json()
        assert body["code"] == 1001

    def test_rotate_rejects_setting_scope(self, auth_client):
        body = auth_client.post(f"{URL}/rotate", {"key": "AI_API_KEY", "scope": "setting"}, format="json").json()
        assert body["code"] == 1001

    def test_rotate_missing_key(self, auth_client):
        assert auth_client.post(f"{URL}/rotate", {}, format="json").json()["code"] == 1001

    def test_rotate_unregistered_key(self, auth_client):
        body = auth_client.post(f"{URL}/rotate", {"key": "WEB_SITE_CONFIG"}, format="json").json()
        assert body["code"] == 1001

    def test_rotate_writes_audit(self, auth_client):
        from system.models import OperationLog

        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        auth_client.post(f"{URL}/rotate", {"key": "SCIM_TOKEN"}, format="json")
        assert OperationLog.objects.filter(module="system:credential").exists()


class TestModelFieldRotate:
    def _make_subscription(self, secret="old-secret"):
        from system.models import WebhookSubscription
        from system.utils.webhook import encrypt_secret

        return WebhookSubscription.objects.create(
            name=f"sub-{secret}", url="https://example.com/hook", secret=encrypt_secret(secret), events=[]
        )

    def test_rotate_model_field_regenerates_and_encrypts(self, auth_client):
        from system.utils.webhook import decrypt_secret

        sub = self._make_subscription("old-secret")
        body = auth_client.post(
            f"{URL}/rotate",
            {"key": "WebhookSubscription.secret", "scope": "model_field"},
            format="json",
        ).json()
        assert body["code"] == 1000
        assert body["data"]["action"] == "rotate"
        sub.refresh_from_db()
        assert sub.secret.startswith("v3:")
        assert decrypt_secret(sub.secret) != "old-secret"

    def test_rotate_model_field_rejects_non_whitelisted(self, auth_client):
        """拒绝任意模型字段注入。"""
        self._make_subscription()
        body = auth_client.post(
            f"{URL}/rotate",
            {"key": "WebhookSubscription.url", "scope": "model_field"},
            format="json",
        ).json()
        assert body["code"] == 1001

    def test_rotate_model_field_rejects_non_rotatable(self, auth_client):
        """外部签发字段（AI api_key）不可原地轮换。"""
        body = auth_client.post(
            f"{URL}/rotate",
            {"key": "AiProfile.api_key", "scope": "model_field"},
            format="json",
        ).json()
        assert body["code"] == 1001

    def test_rotate_model_field_unprivileged_forbidden(self, normal_user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert (
            client.post(
                f"{URL}/rotate",
                {"key": "WebhookSubscription.secret", "scope": "model_field"},
                format="json",
            ).status_code
            == 403
        )


class TestCredentialRotationTracking:
    """总览行的轮换追踪：用途提示、上次轮换时间（审计回溯）、建议轮换标记。"""

    def test_used_by_hints_present(self, auth_client):
        data = auth_client.get(f"{URL}/overview").json()["data"]
        configs = {row["name"]: row for row in data["system_configs"]}
        assert "SCIM" in configs["SCIM_TOKEN"]["used_by"]
        fields = {row["name"]: row for row in data["model_fields"]}
        assert "webhook" in fields["WebhookSubscription.secret"]["used_by"].lower()

    def test_never_rotated_marks_overdue_once_configured(self, auth_client):
        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "v1"})
        row = next(
            r for r in auth_client.get(f"{URL}/overview").json()["data"]["system_configs"] if r["name"] == "SCIM_TOKEN"
        )
        assert row["rotate_overdue"] is True
        assert row["last_rotated"] == ""

    def test_after_rotate_overdue_cleared(self, auth_client):
        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "v1"})
        resp = auth_client.post(f"{URL}/rotate", {"key": "SCIM_TOKEN", "scope": "system_config"}, format="json")
        assert resp.json()["code"] == 1000
        row = next(
            r for r in auth_client.get(f"{URL}/overview").json()["data"]["system_configs"] if r["name"] == "SCIM_TOKEN"
        )
        assert row["last_rotated"] != ""
        assert row["rotate_overdue"] is False

    def test_non_rotatable_rows_have_no_overdue(self, auth_client):
        data = auth_client.get(f"{URL}/overview").json()["data"]
        configs = {row["name"]: row for row in data["system_configs"]}
        assert configs["OAUTH_PROVIDERS"]["rotate_overdue"] is False
        assert configs["OAUTH_PROVIDERS"]["last_rotated"] == ""
