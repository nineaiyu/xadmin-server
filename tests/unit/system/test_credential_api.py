# -*- coding: utf-8 -*-
"""凭据与密钥 API（P-3）：只读聚合 / 轮换 / 权限门控 / 值不外泄。"""

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

    def test_never_leaks_values(self, auth_client):
        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        body = auth_client.get(f"{URL}/overview").json()["data"]
        assert "plain-token" not in json.dumps(body, ensure_ascii=False)
        assert "SCIM_TOKEN" in body["plaintext"]

    def test_unprivileged_user_forbidden(self, normal_user):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        assert client.get(f"{URL}/overview").status_code == 403


class TestCredentialRotate:
    def test_rotate_encrypts_plaintext(self, auth_client):
        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        body = auth_client.post(f"{URL}/rotate", {"key": "SCIM_TOKEN"}, format="json").json()
        assert body["code"] == 1000
        assert body["data"]["action"] == "encrypt"
        assert SystemConfig.objects.get(key="SCIM_TOKEN").value.startswith("v3:")

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
