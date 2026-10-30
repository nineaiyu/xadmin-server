# -*- coding: utf-8 -*-
"""第三方登录配置 API：鉴权 / 密钥掩码与沿用 / 结构校验 / OIDC discovery 测试。"""

from unittest import mock

import pytest

from common.core.config import SysConfig
from system.models import SystemConfig

pytestmark = pytest.mark.django_db

OAUTH_BASE = "/api/settings/oauth/providers"

PROVIDER = {
    "key": "corp",
    "name": "Corp SSO",
    "flavor": "oauth2",
    "enabled": True,
    "client_id": "cid",
    "client_secret": "corp-secret",
    "authorize_url": "https://idp.example.com/authorize",
    "token_url": "https://idp.example.com/token",
    "userinfo_url": "https://idp.example.com/userinfo",
    "scope": "openid profile email",
    "subject_field": "sub",
}

OIDC_PROVIDER = {
    "key": "oidc-corp",
    "name": "OIDC Corp",
    "flavor": "oidc",
    "enabled": False,
    "client_id": "oidc-cid",
    "client_secret": "oidc-secret",
    "issuer": "https://idp.example.com",
}


class TestAuthz:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(OAUTH_BASE).status_code == 401

    def test_normal_user_without_permission_rejected(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(OAUTH_BASE).status_code == 403
        assert api_client.patch(OAUTH_BASE, {"providers": []}, format="json").status_code == 403

    def test_superuser_allowed(self, auth_client):
        assert auth_client.get(OAUTH_BASE).status_code == 200


class TestRetrieveAndSave:
    def test_retrieve_masks_secret_and_returns_flavors(self, auth_client):
        data = auth_client.get(OAUTH_BASE).json()["data"]
        assert data["providers"] == []
        flavors = {item["key"] for item in data["flavors"]}
        assert {"oauth2", "oidc", "dingtalk", "wecom", "feishu"} <= flavors

    def test_save_encrypts_secret_and_masks_response(self, auth_client):
        response = auth_client.patch(OAUTH_BASE, {"providers": [PROVIDER]}, format="json")
        assert response.status_code == 200, response.data
        data = response.json()["data"]
        saved = data["providers"][0]
        assert saved["client_secret_configured"] is True
        assert "client_secret" not in saved
        row = SystemConfig.objects.get(key="OAUTH_PROVIDERS")
        assert "corp-secret" not in str(row.value)
        assert SysConfig.OAUTH_PROVIDERS[0]["client_secret"] == "corp-secret"

    def test_blank_secret_keeps_existing(self, auth_client):
        auth_client.patch(OAUTH_BASE, {"providers": [PROVIDER]}, format="json")
        payload = dict(PROVIDER, name="Corp SSO Renamed", client_secret="")
        response = auth_client.patch(OAUTH_BASE, {"providers": [payload]}, format="json")
        assert response.status_code == 200, response.data
        saved = response.json()["data"]["providers"][0]
        assert saved["name"] == "Corp SSO Renamed"
        assert saved["client_secret_configured"] is True
        assert SysConfig.OAUTH_PROVIDERS[0]["client_secret"] == "corp-secret"

    def test_enabled_without_secret_rejected(self, auth_client):
        payload = dict(PROVIDER, client_secret="")
        response = auth_client.patch(OAUTH_BASE, {"providers": [payload]}, format="json")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 1001
        assert "client_secret" in body["detail"]

    def test_duplicate_key_rejected(self, auth_client):
        response = auth_client.patch(OAUTH_BASE, {"providers": [PROVIDER, PROVIDER]}, format="json")
        assert response.status_code == 200
        assert response.json()["code"] == 1001


class TestDiscovery:
    def test_non_oidc_rejected(self, auth_client):
        body = auth_client.post(f"{OAUTH_BASE}/test", {"provider": PROVIDER}, format="json").json()
        assert body["code"] == 1001

    def test_oidc_requires_issuer(self, auth_client):
        provider = dict(OIDC_PROVIDER, issuer="")
        body = auth_client.post(f"{OAUTH_BASE}/test", {"provider": provider}, format="json").json()
        assert body["code"] == 1001

    def test_oidc_discovery_returns_endpoints(self, auth_client):
        meta = {
            "issuer": "https://idp.example.com",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": "https://idp.example.com/jwks",
            "scopes_supported": ["openid", "profile"],
            "claims_supported": ["sub", "email"],
        }
        with mock.patch("settings.views.oauth.discovery_metadata", return_value=meta):
            body = auth_client.post(f"{OAUTH_BASE}/test", {"provider": OIDC_PROVIDER}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["jwks_uri"] == "https://idp.example.com/jwks"

    def test_oidc_discovery_error_is_readable(self, auth_client):
        from identity.utils.oauth import OAuthError

        with mock.patch(
            "settings.views.oauth.discovery_metadata", side_effect=OAuthError("Failed to contact the identity provider")
        ):
            body = auth_client.post(f"{OAUTH_BASE}/test", {"provider": OIDC_PROVIDER}, format="json").json()
        assert body["code"] == 1001
        assert "Failed to contact" in body["detail"]
