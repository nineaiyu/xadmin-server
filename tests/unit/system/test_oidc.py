# -*- coding: utf-8 -*-
"""标准 OIDC守护测试：discovery / id_token 验签 / claims 映射 / 组角色同步 / 回调全链路。

全部离线：JWKS 与 token 用本地生成的 RSA 密钥对自造，HTTP 走可注入 stub client。
"""

import json
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.core.exceptions import ValidationError
from jwt.algorithms import RSAAlgorithm
from rest_framework.test import APIClient

from system.models import UserInfo, UserRole
from system.utils.oauth import OAuthError, issue_nonce, issue_state, validate_providers
from system.utils.oidc import (
    claims_to_userinfo,
    fetch_oidc_identity,
    resolve_endpoints,
    sync_group_roles,
    verify_id_token,
)

pytestmark = pytest.mark.django_db

ISSUER = "https://idp.example.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URL = f"{ISSUER}/jwks"
TOKEN_URL = f"{ISSUER}/token"
AUTHORIZE_URL = f"{ISSUER}/authorize"
CLIENT_ID = "xadmin-client"


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _StubClient:
    """按 URL 路由的离线 HTTP 客户端（route 可为载荷或返回载荷的可调用对象）。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _resolve(self, url):
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f"unexpected request: {url}")
        return route() if callable(route) else route

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return _StubResponse(self._resolve(url))

    def post(self, url, data=None, timeout=None):
        self.calls.append(url)
        return _StubResponse(self._resolve(url))


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid="k1"):
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _sign(private_key, claims, kid="k1", algorithm="RS256"):
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": kid})


def _provider(**overrides):
    provider = {
        "key": "corp",
        "name": "Corp SSO",
        "flavor": "oidc",
        "enabled": True,
        "client_id": CLIENT_ID,
        "client_secret": "secret",
        "issuer": ISSUER,
        "subject_field": "sub",
        "groups_field": "groups",
        "group_role_map": {},
        "nickname_claim": "name",
        "email_claim": "email",
        "phone_claim": "phone_number",
    }
    provider.update(overrides)
    return provider


def _discovery_payload(issuer=ISSUER, jwks_uri=JWKS_URL):
    return {
        "issuer": issuer,
        "authorization_endpoint": AUTHORIZE_URL,
        "token_endpoint": TOKEN_URL,
        "jwks_uri": jwks_uri,
    }


def _claims(**overrides):
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-1",
        "exp": 4102444800,  # 2100-01-01
        "iat": 1700000000,
        "name": "张三",
        "email": "zhangsan@example.com",
        "phone_number": "13800000000",
        "groups": ["ops-team"],
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestDiscovery:
    def test_resolve_endpoints_merges_discovery_and_caches(self):
        client = _StubClient({DISCOVERY_URL: _discovery_payload()})
        provider = _provider()
        endpoints = resolve_endpoints(provider, http_client=client)
        assert endpoints["authorize_url"] == AUTHORIZE_URL
        assert endpoints["token_url"] == TOKEN_URL
        assert endpoints["jwks_uri"] == JWKS_URL
        assert endpoints["issuer"] == ISSUER

        client.routes.clear()  # 缓存命中：不应再发请求
        assert resolve_endpoints(provider, http_client=client)["jwks_uri"] == JWKS_URL

    def test_explicit_endpoints_win_over_discovery(self):
        client = _StubClient({DISCOVERY_URL: _discovery_payload()})
        provider = _provider(
            authorize_url="https://custom.example.com/authorize", jwks_uri="https://custom.example.com/jwks"
        )
        endpoints = resolve_endpoints(provider, http_client=client)
        assert endpoints["authorize_url"] == "https://custom.example.com/authorize"
        assert endpoints["jwks_uri"] == "https://custom.example.com/jwks"
        assert endpoints["token_url"] == TOKEN_URL

    def test_discovery_failure_is_readable(self):
        class _Boom:
            def get(self, url, headers=None, timeout=None):
                raise RuntimeError("network down")

        with pytest.raises(OAuthError):
            resolve_endpoints(_provider(), http_client=_Boom())


class TestVerifyIdToken:
    def test_valid_token_returns_claims(self):
        private_key = _keypair()
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: {"keys": [_jwk(private_key)]}})
        token = _sign(private_key, _claims(nonce="n-1"))
        claims = verify_id_token(_provider(), token, nonce="n-1", http_client=client)
        assert claims["sub"] == "user-1"

    def test_tampered_token_rejected(self):
        private_key = _keypair()
        other_key = _keypair()
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: {"keys": [_jwk(other_key)]}})
        token = _sign(private_key, _claims())
        with pytest.raises(OAuthError):
            verify_id_token(_provider(), token, http_client=client)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"exp": 1000000000},  # 过期
            {"aud": "other-client"},  # 受众不符
            {"iss": "https://evil.example.com"},  # 签发者不符
        ],
    )
    def test_invalid_claims_rejected(self, overrides):
        private_key = _keypair()
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: {"keys": [_jwk(private_key)]}})
        token = _sign(private_key, _claims(**overrides))
        with pytest.raises(OAuthError):
            verify_id_token(_provider(), token, http_client=client)

    def test_nonce_mismatch_rejected(self):
        private_key = _keypair()
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: {"keys": [_jwk(private_key)]}})
        token = _sign(private_key, _claims(nonce="n-1"))
        with pytest.raises(OAuthError):
            verify_id_token(_provider(), token, nonce="n-2", http_client=client)

    def test_algorithm_confusion_rejected(self):
        """alg=none（或 HS*）一律拒绝：防算法混淆绕过验签。"""
        none_token = jwt.encode(_claims(), key="", algorithm="none", headers={"kid": "k1"})
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: {"keys": []}})
        with pytest.raises(OAuthError):
            verify_id_token(_provider(), none_token, http_client=client)

    def test_jwks_refreshed_on_key_rotation(self):
        """kid 未命中（密钥轮换）：强制刷新一次 JWKS 后成功验签。"""
        old_key, new_key = _keypair(), _keypair()
        responses = [
            {"keys": [_jwk(old_key, kid="old")]},
            {"keys": [_jwk(new_key, kid="new")]},
        ]
        client = _StubClient({DISCOVERY_URL: _discovery_payload(), JWKS_URL: lambda: responses.pop(0)})
        token = _sign(new_key, _claims(), kid="new")
        claims = verify_id_token(_provider(), token, http_client=client)
        assert claims["sub"] == "user-1"

    def test_fetch_oidc_identity_requires_id_token(self):
        with pytest.raises(OAuthError):
            fetch_oidc_identity(_provider(), {"access_token": "at"})


class TestClaimsMapping:
    def test_default_claims_mapping(self):
        userinfo = claims_to_userinfo(_provider(), _claims())
        assert userinfo["sub"] == "user-1"
        assert userinfo["nickname"] == "张三"
        assert userinfo["email"] == "zhangsan@example.com"
        assert userinfo["phone"] == "13800000000"
        assert userinfo["groups"] == ["ops-team"]

    def test_custom_claim_names_and_string_groups(self):
        provider = _provider(
            nickname_claim="preferred_username",
            email_claim="mail",
            phone_claim="mobile",
            groups_field="roles",
        )
        claims = _claims(preferred_username="lisi", mail="lisi@example.com", mobile="139", roles="a, b")
        userinfo = claims_to_userinfo(provider, claims)
        assert userinfo["nickname"] == "lisi"
        assert userinfo["email"] == "lisi@example.com"
        assert userinfo["phone"] == "139"
        assert userinfo["groups"] == ["a", "b"]

    def test_group_role_map_is_only_reference_for_sync(self, superuser):
        ops = UserRole.objects.create(code="ops", name="运维", is_active=True)
        audit = UserRole.objects.create(code="audit", name="审计", is_active=True)
        hr = UserRole.objects.create(code="hr", name="人事", is_active=True)
        # hr 不在映射里（管理员手工授予）：同步不得触碰；audit 在映射里但 claims 未命中 → 移除
        superuser.roles.add(audit, hr)

        provider = _provider(group_role_map={"ops-team": "ops", "audit-team": ["audit"]})
        synced = sync_group_roles(superuser, provider, {"groups": ["ops-team"]})
        assert synced == ["ops"]
        assert set(superuser.roles.values_list("code", flat=True)) == {"hr", "ops"}

        # 组切换：映射内角色按 claims 替换（ops 解除关联，audit 命中 DN 形态加回）
        sync_group_roles(superuser, provider, {"groups": ["cn=audit-team,ou=people"]})
        assert set(superuser.roles.values_list("code", flat=True)) == {"audit", "hr"}
        assert ops.pk and audit.pk  # 角色对象仍存在（只解除关联，不删角色）

    def test_empty_mapping_is_noop(self, superuser):
        assert sync_group_roles(superuser, _provider(), {"groups": ["ops-team"]}) == []


class TestProviderValidation:
    def test_oidc_requires_issuer_or_explicit_endpoints(self):
        with pytest.raises(ValidationError):
            validate_providers(
                [{"key": "corp", "name": "Corp", "flavor": "oidc", "client_id": "c", "client_secret": "s"}]
            )

    def test_oidc_group_role_map_must_be_object(self):
        with pytest.raises(ValidationError):
            validate_providers(
                [
                    {
                        "key": "corp",
                        "name": "Corp",
                        "flavor": "oidc",
                        "client_id": "c",
                        "client_secret": "s",
                        "issuer": ISSUER,
                        "group_role_map": ["ops"],
                    }
                ]
            )

    def test_oidc_issuer_must_use_https(self):
        with pytest.raises(ValidationError):
            validate_providers(
                [
                    {
                        "key": "corp",
                        "name": "Corp",
                        "flavor": "oidc",
                        "client_id": "c",
                        "client_secret": "s",
                        "issuer": "http://idp.internal",
                    }
                ]
            )

    def test_valid_oidc_provider_passes(self):
        providers = validate_providers(
            [
                {
                    "key": "corp",
                    "name": "Corp",
                    "flavor": "oidc",
                    "client_id": "c",
                    "client_secret": "s",
                    "issuer": ISSUER,
                    "group_role_map": {"ops-team": "ops"},
                    "enabled": True,
                }
            ]
        )
        assert providers[0]["flavor"] == "oidc"
        assert providers[0]["groups_field"] == "groups"


class TestCallbackIntegration:
    """回调全链路：discovery → 换码 → id_token 验签 → 建号 → 组角色同步 → 签发 token。"""

    def test_auto_create_user_with_group_role(self, monkeypatch):
        from common.core.config import SysConfig

        private_key = _keypair()
        role = UserRole.objects.create(code="ops", name="运维", is_active=True)
        provider = _provider(enabled=True, auto_create=True, group_role_map={"ops-team": "ops"})
        monkeypatch.setattr(type(SysConfig), "OAUTH_PROVIDERS", property(lambda self: [provider]), raising=False)
        state = issue_state("corp")
        nonce_holder = {"value": issue_nonce(state)}
        routes = {
            DISCOVERY_URL: _discovery_payload(),
            JWKS_URL: {"keys": [_jwk(private_key)]},
            TOKEN_URL: lambda: {
                "access_token": "at",
                "id_token": _sign(private_key, _claims(nonce=nonce_holder["value"])),
            },
        }
        monkeypatch.setattr("system.utils.oauth._default_client", lambda: _StubClient(routes))

        # 回调是匿名可达端点（白名单整段前缀），这里直接走 APIClient
        response = APIClient().get(f"/api/system/auth/oauth/corp/callback?code=code-1&state={state}")
        assert response.status_code == 200, response.data
        assert response.data.get("code") == 1000, response.data

        user = UserInfo.objects.get(username__startswith="corp_")
        assert user.nickname == "张三"
        assert user.email == "zhangsan@example.com"
        assert list(user.roles.values_list("code", flat=True)) == [role.code]


class TestAuthorizeUrl:
    def test_authorize_url_contains_nonce_and_discovered_endpoint(self, monkeypatch):
        from common.core.config import SysConfig

        provider = _provider(enabled=True)
        monkeypatch.setattr(type(SysConfig), "OAUTH_PROVIDERS", property(lambda self: [provider]), raising=False)
        client = _StubClient({DISCOVERY_URL: _discovery_payload()})
        monkeypatch.setattr("system.utils.oauth._default_client", lambda: client)

        response = APIClient().get("/api/system/auth/oauth/corp/authorize")
        assert response.status_code == 200, response.data
        url = response.data["data"]["url"]
        params = parse_qs(urlparse(url).query)
        assert url.startswith(AUTHORIZE_URL)
        assert params["nonce"], "OIDC 授权地址必须带 nonce"
        assert params["scope"] == ["openid profile email"]
