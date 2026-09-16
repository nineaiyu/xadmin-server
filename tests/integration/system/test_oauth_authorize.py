# -*- coding: utf-8 -*-
"""OAuth 2.0 授权码模式：授权流程 / PKCE / refresh 轮换 / 撤销 / 四级授权联动。

纪律：token/revoke 匿名可达（凭证即身份）；access 是 PAT（creator = 授权用户），
因此 B1 四级授权对 OAuth 凭证同样生效（关键红线：OAuth 不绕过应用授权）。
"""

import base64
import hashlib
import secrets

import pytest
from rest_framework.test import APIClient

from system.models.log import OperationLog
from system.models.token import OAuthRefreshToken, PersonalAccessToken

pytestmark = pytest.mark.django_db

APPS_URL = "/api/system/api-applications"
OAUTH_URL = "/api/system/open/oauth"
CALLBACK = "https://example.com/cb"
USER_URL = "/api/system/user"


def _create_application(client, **payload):
    data = {"name": "OAuth 应用", "rate_limit_per_minute": 0, "callback_urls": [CALLBACK]}
    data.update(payload)
    resp = client.post(APPS_URL, data, format="json")
    assert resp.status_code == 201, resp.data
    return resp.data["data"]


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_params(application, **overrides):
    params = {
        "client_id": application["client_id"],
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "state": "st-123",
    }
    params.update(overrides)
    return params


def _approve(client, application, **overrides):
    payload = _authorize_params(application, **overrides)
    payload["approved"] = True
    resp = client.post(f"{OAUTH_URL}/approve", payload, format="json")
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]


def _exchange(application, code, verifier="", redirect_uri=CALLBACK):
    return APIClient().post(
        f"{OAUTH_URL}/token",
        {
            "grant_type": "authorization_code",
            "client_id": application["client_id"],
            "client_secret": application["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        format="json",
    )


def _pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Pat {raw_token}")
    return client


class TestAuthorizeFlow:
    def test_authorize_returns_consent_data(self, auth_client):
        application = _create_application(auth_client, scopes=["api/system/user"])
        resp = auth_client.get(f"{OAUTH_URL}/authorize", _authorize_params(application))
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["application"]["client_id"] == application["client_id"]
        assert data["scopes"] == ["api/system/user"]
        assert data["state"] == "st-123"
        assert data["user"]["username"] == "admin"

    def test_authorize_requires_login(self, api_client):
        resp = api_client.get(f"{OAUTH_URL}/authorize", {"client_id": "app_x", "redirect_uri": CALLBACK})
        assert resp.status_code == 401

    def test_authorize_rejects_unregistered_redirect(self, auth_client):
        application = _create_application(auth_client)
        resp = auth_client.get(
            f"{OAUTH_URL}/authorize", _authorize_params(application, redirect_uri="https://evil.com/cb")
        )
        assert resp.status_code == 400
        assert resp.data["data"]["error"] == "invalid_request"

    def test_authorize_rejects_out_of_scope(self, auth_client):
        application = _create_application(auth_client, scopes=["api/system/user"])
        resp = auth_client.get(f"{OAUTH_URL}/authorize", _authorize_params(application, scope="api/system/dept"))
        assert resp.status_code == 400
        assert resp.data["data"]["error"] == "invalid_scope"

    def test_deny_returns_access_denied_without_code(self, auth_client):
        application = _create_application(auth_client)
        payload = _authorize_params(application)
        payload["approved"] = False
        resp = auth_client.post(f"{OAUTH_URL}/approve", payload, format="json")
        assert resp.data["data"]["error"] == "access_denied"
        assert resp.data["data"]["code"] == ""
        assert OperationLog.objects.filter(module="OAuth", response_result="denied").exists()

    def test_full_flow_issues_tokens(self, auth_client, superuser):
        application = _create_application(auth_client, scopes=["api/system/user"])
        verifier, challenge = _pkce_pair()
        approved = _approve(auth_client, application, code_challenge=challenge, code_challenge_method="S256")
        assert OperationLog.objects.filter(module="OAuth", response_result="approved").exists()

        resp = _exchange(application, approved["code"], verifier=verifier)
        assert resp.data["code"] == 1000, resp.data
        payload = resp.data["data"]
        assert payload["token_type"] == "Pat"
        assert payload["refresh_token"].startswith("aort_")
        assert payload["scope"] == ["api/system/user"]

        # access 走既有认证链（creator = 授权用户 = 超管），scope 生效
        client = _pat_client(payload["access_token"])
        assert client.get(USER_URL).status_code == 200
        assert client.get(APPS_URL).status_code == 403
        token_row = PersonalAccessToken.objects.get(name__startswith="oauth:")
        assert token_row.creator_id == superuser.pk

    def test_pkce_mismatch_rejected(self, auth_client):
        application = _create_application(auth_client)
        _verifier, challenge = _pkce_pair()
        approved = _approve(auth_client, application, code_challenge=challenge, code_challenge_method="S256")
        resp = _exchange(application, approved["code"], verifier="wrong-verifier")
        assert resp.status_code == 400
        assert resp.data["data"]["error"] == "invalid_grant"

    def test_authorization_code_is_single_use(self, auth_client):
        application = _create_application(auth_client)
        approved = _approve(auth_client, application)
        assert _exchange(application, approved["code"]).data["code"] == 1000
        second = _exchange(application, approved["code"])
        assert second.status_code == 400
        assert second.data["data"]["error"] == "invalid_grant"


class TestRefreshAndRevoke:
    def _issue(self, auth_client):
        application = _create_application(auth_client)
        approved = _approve(auth_client, application)
        return application, _exchange(application, approved["code"]).data["data"]

    def test_refresh_rotates_tokens(self, auth_client):
        application, issued = self._issue(auth_client)
        old_refresh = issued["refresh_token"]
        old_access = issued["access_token"]
        resp = APIClient().post(
            f"{OAUTH_URL}/token",
            {
                "grant_type": "refresh_token",
                "client_id": application["client_id"],
                "client_secret": application["client_secret"],
                "refresh_token": old_refresh,
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        new_payload = resp.data["data"]
        assert new_payload["refresh_token"] != old_refresh
        # 旧 refresh 与旧 access 同时失效（一次性轮换）
        assert _pat_client(old_access).get(USER_URL).status_code == 401
        reuse = APIClient().post(
            f"{OAUTH_URL}/token",
            {
                "grant_type": "refresh_token",
                "client_id": application["client_id"],
                "client_secret": application["client_secret"],
                "refresh_token": old_refresh,
            },
            format="json",
        )
        assert reuse.status_code == 400
        assert _pat_client(new_payload["access_token"]).get(USER_URL).status_code == 200

    def test_revoke_refresh_disables_access(self, auth_client):
        application, issued = self._issue(auth_client)
        resp = APIClient().post(
            f"{OAUTH_URL}/revoke",
            {
                "client_id": application["client_id"],
                "client_secret": application["client_secret"],
                "token": issued["refresh_token"],
            },
            format="json",
        )
        assert resp.data["data"]["revoked"] is True
        assert _pat_client(issued["access_token"]).get(USER_URL).status_code == 401
        assert OAuthRefreshToken.objects.filter(is_revoked=True).count() == 1

    def test_revoke_unknown_token_returns_ok(self, auth_client):
        application = _create_application(auth_client)
        resp = APIClient().post(
            f"{OAUTH_URL}/revoke",
            {
                "client_id": application["client_id"],
                "client_secret": application["client_secret"],
                "token": "aort_unknown",
            },
            format="json",
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["revoked"] is False


class TestGrantEnforcementOnOAuthToken:
    def test_oauth_access_respects_application_grants(self, auth_client, menu_factory):
        """红线：OAuth 凭证不绕过 B1 四级授权（应用有规则时同样白名单）。"""
        from system.models.field import ModelLabelField

        model_label = ModelLabelField.objects.create(name="system.userinfo", label="用户信息")
        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        menu.model.add(model_label)

        application = _create_application(auth_client)
        put = auth_client.put(
            f"{APPS_URL}/{application['pk']}/grants",
            {"grants": [{"model": "system.userinfo", "actions": ["list"]}]},
            format="json",
        )
        assert put.data["code"] == 1000
        approved = _approve(auth_client, application)
        issued = _exchange(application, approved["code"]).data["data"]
        client = _pat_client(issued["access_token"])
        assert client.get(USER_URL).status_code == 200
        assert client.get("/api/system/dept").status_code == 403
