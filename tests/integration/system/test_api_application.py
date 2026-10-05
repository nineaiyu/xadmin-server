# -*- coding: utf-8 -*-
"""开放平台雏形：应用 CRUD / client-credentials 换发 / 凭证链路 / 限流 / 回调。

关键口径与测试纪律：
- 换发 = 轮换：明文不可回读，旧凭证即时失效；
- 应用凭证以 owner 身份走既有 PAT 认证链（scopes 生效，超管也受限）；
- 应用停用/过期、按应用限流在认证处即时生效；
- **凭证类断言必须用独立 APIClient**：`auth_client` 是 force_authenticate 的同一个实例，
  会把请求直接认成超管，PAT 头根本不参与认证（会掩盖失效/限流行为）；
- 探针优先用写接口：读接口可能命中响应缓存（不经过认证，计数与失效都测不出来）。
"""

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from identity.models.token import ApiApplication, PersonalAccessToken

pytestmark = pytest.mark.django_db

APPS_URL = "/api/system/api-applications"
TOKEN_URL = "/api/system/open/token"


def _create_application(client, **payload):
    data = {"name": "E2E 应用", "rate_limit_per_minute": 0}
    data.update(payload)
    resp = client.post(APPS_URL, data, format="json")
    assert resp.status_code == 201, resp.data
    return resp.data["data"]


def _issue_token(application, secret=None):
    """换发凭证（匿名客户端，凭证即身份）。"""
    return APIClient().post(
        TOKEN_URL,
        {"client_id": application["client_id"], "client_secret": secret or application["client_secret"]},
        format="json",
    )


def _pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Pat {raw_token}")
    return client


class TestApplicationManagement:
    def test_create_returns_credentials_once(self, auth_client):
        application = _create_application(auth_client)
        assert application["client_id"].startswith("app_")
        assert application["client_secret"].startswith("aps_")
        assert application["callback_secret"].startswith("apc_")
        # 列表/详情不回传明文（只留前缀）
        detail = auth_client.get(f"{APPS_URL}/{application['pk']}")
        assert "client_secret" not in detail.data["data"]
        assert detail.data["data"]["client_secret_prefix"] == application["client_secret"][:12]
        row = ApiApplication.objects.get(pk=application["pk"])
        assert not row.client_secret_hash.startswith("aps_")  # 库内只有哈希

    def test_callback_url_requires_https(self, auth_client):
        resp = auth_client.post(
            APPS_URL,
            {"name": "非法回调", "callback_urls": ["http://example.com/hook"]},
            format="json",
        )
        assert resp.status_code == 400

    def test_regenerate_secret_rotates_credentials_and_tokens(self, auth_client):
        application = _create_application(auth_client)
        old_token = _issue_token(application).data["data"]["access_token"]

        regenerated = auth_client.post(f"{APPS_URL}/{application['pk']}/regenerate-secret")
        assert regenerated.data["code"] == 1000
        rotated = {**application, **regenerated.data["data"]}
        assert rotated["client_secret"] != application["client_secret"]

        # 旧 secret 与旧凭证同时失效（写接口探针：读接口可能命中响应缓存）
        assert _issue_token(application).status_code == 401
        assert _pat_client(old_token).post(APPS_URL, {"name": "probe"}, format="json").status_code == 401
        # 新 secret 可用
        assert _issue_token(rotated).data["code"] == 1000


class TestScopeOptions:
    """应用「接口范围」选项：与令牌同源（权限菜单 × 请求用户），供管理页勾选。

    选项是白名单元数据（设置见 `server/settings/custom.py`）：返回的只是「当前用户可
    授权的接口」（本人权限菜单派生，无业务数据行），查看/编辑权限点分离时勾选器仍可用。
    """

    def test_scope_options_list_authorized_apis(self, auth_client, menu_factory):
        menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        resp = auth_client.get(f"{APPS_URL}/scope-options")
        assert resp.data["code"] == 1000
        payload = resp.data["data"]
        options = [option for group in payload["groups"] for option in group["options"]]
        assert "GET ^/api/system/user/?$" in [option["value"] for option in options]
        for option in options:
            assert option["method"] and option["path"] and option["label"] and option["code"]

    def test_scope_options_requires_login(self, api_client):
        assert api_client.get(f"{APPS_URL}/scope-options").status_code == 401

    def test_scope_options_readable_without_api_app_permission(self, normal_user):
        """白名单口径：无 API 应用权限的登录用户也能读自己的可授权范围（空清单）。"""
        client = APIClient()
        client.force_authenticate(user=normal_user)
        resp = client.get(f"{APPS_URL}/scope-options")
        assert resp.data["code"] == 1000
        assert resp.data["data"]["groups"] == []


class TestClientCredentials:
    def test_issue_and_authenticate_with_application_token(self, auth_client):
        application = _create_application(auth_client)
        resp = _issue_token(application)
        assert resp.data["code"] == 1000
        payload = resp.data["data"]
        assert payload["token_type"] == "Pat"
        assert payload["expires_in"] and payload["expires_in"] > 0

        token_row = PersonalAccessToken.objects.get(api_application_id=application["pk"])
        assert token_row.creator_id is not None  # 凭证归属 owner，审计可回溯
        assert _pat_client(payload["access_token"]).get("/api/system/userinfo").status_code == 200

    def test_wrong_secret_rejected(self, auth_client):
        application = _create_application(auth_client)
        assert _issue_token(application, secret="aps_wrong").status_code == 401

    def test_disabled_application_rejects_issue_and_existing_token(self, auth_client):
        application = _create_application(auth_client)
        issued = _issue_token(application).data["data"]["access_token"]
        auth_client.patch(f"{APPS_URL}/{application['pk']}", {"is_active": False}, format="json")

        assert _issue_token(application).status_code == 401
        assert _pat_client(issued).post(APPS_URL, {"name": "probe"}, format="json").status_code == 401

    def test_expired_application_rejected(self, auth_client):
        application = _create_application(auth_client)
        row = ApiApplication.objects.get(pk=application["pk"])
        row.expired_at = timezone.now() - timezone.timedelta(minutes=1)
        row.save(update_fields=["expired_at"])
        assert _issue_token(application).status_code == 401

    def test_application_scopes_restrict_paths(self, auth_client):
        """scope 限制凭证可访问的路径（超管 owner 同样受限，口径见 IsAuthenticated）。"""
        application = _create_application(auth_client, scopes=["api/system/userinfo"])
        token = _issue_token(application).data["data"]["access_token"]
        client = _pat_client(token)
        assert client.get("/api/system/userinfo").status_code == 200
        assert client.get(APPS_URL).status_code == 403

    def test_rate_limit_per_application(self, auth_client):
        cache.clear()
        application = _create_application(auth_client, rate_limit_per_minute=1)
        token = _issue_token(application).data["data"]["access_token"]
        client = _pat_client(token)
        assert client.post(APPS_URL, {"name": "probe-1"}, format="json").status_code == 201
        blocked = client.post(APPS_URL, {"name": "probe-2"}, format="json")
        assert blocked.status_code == 429
        # 应用限流保留可读文案（ReadableThrottled），不归一为通用「手速太快」
        from django.utils.translation import gettext as _

        assert str(blocked.data["detail"]) == _("Rate limit exceeded for this application")


class TestOpenTokenClientThrottle:
    """换发端点 client 维度专用限流（在全局匿名限流的 IP 维度之上叠加）。

    桶按请求体 client_id 划分，未带 client_id 回退 IP 桶；凭据错误（401）与
    限流（429 + 业务码 999）可区分。
    """

    @staticmethod
    def _patch_rate(monkeypatch, rate):
        from django.conf import settings as dj_settings

        monkeypatch.setitem(dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "open_client", rate)

    def test_throttled_per_client(self, monkeypatch):
        self._patch_rate(monkeypatch, "2/m")
        client = APIClient()
        payload = {"client_id": "app_thr_a", "client_secret": "aps_wrong"}
        assert client.post(TOKEN_URL, payload, format="json").status_code == 401
        assert client.post(TOKEN_URL, payload, format="json").status_code == 401
        blocked = client.post(TOKEN_URL, payload, format="json")
        assert blocked.status_code == 429
        assert blocked.data["code"] == 999  # Throttled 统一业务码（common_exception_handler）

    def test_buckets_isolated_per_client(self, monkeypatch):
        self._patch_rate(monkeypatch, "2/m")
        client = APIClient()
        payload_a = {"client_id": "app_thr_iso_a", "client_secret": "aps_wrong"}
        payload_b = {"client_id": "app_thr_iso_b", "client_secret": "aps_wrong"}
        for _ in range(2):
            assert client.post(TOKEN_URL, payload_a, format="json").status_code == 401
        assert client.post(TOKEN_URL, payload_a, format="json").status_code == 429
        # 另一 client 独立桶：仍是凭据错误（401）而非限流（429）
        assert client.post(TOKEN_URL, payload_b, format="json").status_code == 401

    def test_missing_client_id_falls_back_to_ip_bucket(self, monkeypatch):
        self._patch_rate(monkeypatch, "1/m")
        client = APIClient()
        assert client.post(TOKEN_URL, {"client_secret": "aps_wrong"}, format="json").status_code == 401
        assert client.post(TOKEN_URL, {"client_secret": "aps_wrong"}, format="json").status_code == 429


class TestCallbackProbe:
    def test_test_callback_dispatches_signed_probe(self, auth_client, monkeypatch):
        # 生产路径走 pinned_request（与 webhook 投递同口径）：惰性导入，
        # mock 落在事实源模块 common.utils.outbound 上
        calls = []

        class FakeResponse:
            status_code = 200

        def fake_pinned_request(method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

        monkeypatch.setattr("common.utils.outbound.pinned_request", fake_pinned_request)
        application = _create_application(auth_client, callback_urls=["https://example.com/hook"])
        resp = auth_client.post(f"{APPS_URL}/{application['pk']}/test-callback")
        assert resp.data["code"] == 1000
        results = resp.data["data"]["results"]
        assert results[0]["success"] is True and results[0]["status_code"] == 200
        assert calls and calls[0]["url"] == "https://example.com/hook"
        assert calls[0]["method"] == "POST"
        assert calls[0]["headers"]["X-Webhook-Signature"].startswith("sha256=")
        assert calls[0]["headers"]["X-Webhook-Timestamp"]

    def test_test_callback_keeps_outbound_guard(self, auth_client, monkeypatch):
        """探测与投递链路同口径过出站守卫：私网拒绝（allow_private=False），
        OUTBOUND_ALLOWED_HOSTS 透传放行；守卫拒绝按失败结果返回而非 500。"""
        seen_kwargs = {}

        def fake_pinned_request(method, url, **kwargs):
            seen_kwargs.update(kwargs)
            raise ConnectionError("blocked by outbound guard")

        monkeypatch.setattr("common.utils.outbound.pinned_request", fake_pinned_request)
        application = _create_application(auth_client, callback_urls=["https://example.com/hook"])
        resp = auth_client.post(f"{APPS_URL}/{application['pk']}/test-callback")
        assert resp.data["code"] == 1000
        results = resp.data["data"]["results"]
        assert results[0]["success"] is False
        assert "blocked by outbound guard" in results[0]["detail"]
        assert seen_kwargs["allow_private"] is False
        assert seen_kwargs["allow_loopback"] is True
        assert isinstance(seen_kwargs["allowed_hosts"], tuple)

    def test_test_callback_without_urls(self, auth_client):
        application = _create_application(auth_client)
        resp = auth_client.post(f"{APPS_URL}/{application['pk']}/test-callback")
        assert resp.data["code"] == 1001
        assert resp.data["data"]["results"] == []
