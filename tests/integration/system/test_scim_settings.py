# -*- coding: utf-8 -*-
"""SCIM 设置 API：鉴权 / 密钥不回显 / 开关校验 / 令牌轮换 / 自检 / 同步记录。"""

import pytest

from audit.models import OperationLog
from common.core.config import SysConfig
from system.models import SystemConfig

pytestmark = pytest.mark.django_db

SCIM_BASE = "/api/settings/scim"


class TestAuthz:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(SCIM_BASE).status_code == 401

    def test_normal_user_without_permission_rejected(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(SCIM_BASE).status_code == 403
        assert api_client.patch(SCIM_BASE, {"SCIM_ENABLED": True}, format="json").status_code == 403

    def test_superuser_allowed(self, auth_client):
        assert auth_client.get(SCIM_BASE).status_code == 200


class TestRetrieveAndSave:
    def test_retrieve_never_returns_token(self, auth_client):
        data = auth_client.get(SCIM_BASE).json()["data"]
        assert "SCIM_TOKEN" not in data
        assert data["SCIM_TOKEN_CONFIGURED"] is False
        assert data["SCIM_ENABLED"] is False

    def test_enable_without_token_rejected(self, auth_client):
        response = auth_client.patch(SCIM_BASE, {"SCIM_ENABLED": True}, format="json")
        assert response.status_code == 400
        assert "SCIM_TOKEN" in response.json()

    def test_save_persists_encrypted_token(self, auth_client):
        response = auth_client.patch(
            SCIM_BASE,
            {
                "SCIM_ENABLED": True,
                "SCIM_TOKEN": "scim-secret-token",
                "SCIM_RATE_LIMIT": "120/min",
                "SCIM_DEFAULT_ROLE_CODE": "",
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        row = SystemConfig.objects.get(key="SCIM_TOKEN")
        assert row.value != "scim-secret-token"
        assert "scim-secret-token" not in str(row.value)
        assert SysConfig.SCIM_TOKEN == "scim-secret-token"
        assert SysConfig.SCIM_ENABLED is True
        assert SysConfig.SCIM_RATE_LIMIT == "120/min"
        data = response.json()["data"]
        assert data["SCIM_TOKEN_CONFIGURED"] is True
        assert "SCIM_TOKEN" not in data

    def test_blank_token_keeps_existing(self, auth_client):
        auth_client.patch(
            SCIM_BASE,
            {"SCIM_ENABLED": True, "SCIM_TOKEN": "keep-me", "SCIM_RATE_LIMIT": "600/min"},
            format="json",
        )
        auth_client.patch(SCIM_BASE, {"SCIM_ENABLED": False, "SCIM_TOKEN": ""}, format="json")
        assert SysConfig.SCIM_TOKEN == "keep-me"

    def test_invalid_rate_limit_rejected(self, auth_client):
        response = auth_client.patch(SCIM_BASE, {"SCIM_RATE_LIMIT": "fast"}, format="json")
        assert response.status_code == 400
        assert "SCIM_RATE_LIMIT" in response.json()


class TestRotateToken:
    def test_rotate_returns_plaintext_once_and_persists_cipher(self, auth_client):
        response = auth_client.post(f"{SCIM_BASE}/rotate-token", {}, format="json")
        body = response.json()
        assert body["code"] == 1000
        token = body["data"]["token"]
        assert token
        row = SystemConfig.objects.get(key="SCIM_TOKEN")
        assert token not in str(row.value)
        assert SysConfig.SCIM_TOKEN == token

    def test_rotate_invalidates_previous_token(self, auth_client):
        first = auth_client.post(f"{SCIM_BASE}/rotate-token", {}, format="json").json()["data"]["token"]
        second = auth_client.post(f"{SCIM_BASE}/rotate-token", {}, format="json").json()["data"]["token"]
        assert first != second
        assert SysConfig.SCIM_TOKEN == second


class TestSelfCheck:
    def test_disabled_reports_problem(self, auth_client):
        body = auth_client.post(SCIM_BASE, {}, format="json").json()
        assert body["code"] == 1001
        assert body["data"]["enabled"] is False

    def test_enabled_with_token_is_ready(self, auth_client):
        auth_client.post(f"{SCIM_BASE}/rotate-token", {}, format="json")
        auth_client.patch(SCIM_BASE, {"SCIM_ENABLED": True}, format="json")
        body = auth_client.post(SCIM_BASE, {}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["token_configured"] is True


class TestSyncStatus:
    def test_returns_recent_scim_audits(self, auth_client):
        OperationLog.objects.create(
            module="SCIM:create",
            path="/api/scim/v2/Users",
            method="POST",
            status_code=1000,
            auth_type=OperationLog.AuthType.SCIM,
        )
        OperationLog.objects.create(module="Other", path="/api/system/user", method="GET", status_code=1000)
        body = auth_client.get(f"{SCIM_BASE}/sync-status").json()
        assert body["data"]["total"] == 1
        assert body["data"]["results"][0]["module"] == "SCIM:create"
