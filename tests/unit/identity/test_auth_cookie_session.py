# -*- coding: utf-8 -*-
"""HttpOnly 认证 Cookie 会话（AUTH_COOKIE_HTTPONLY 双模式）守护。

口径（实现见 identity/services/auth_cookie.py）：

- 开关关闭（默认）：token 留在响应体，**不下发** X-Token / X-Refresh-Token Cookie
  （前端自写 JS Cookie 的现状零变化）；仅下发 JS 可读的模式标记 X-Auth-Mode=header；
- 开关开启：两条 token 以 ``HttpOnly; SameSite=Lax`` 下发（HTTPS 时带 Secure），
  寿命 = token 生命周期 - 10s；响应体 ``access`` / ``refresh`` 置空串但**键保留**
  （前端按 ``"access" in data`` 判定登录成功）；
- 刷新端点双读：body refresh 优先，缺失回落 X-Refresh-Token Cookie + Origin 兜底
  （跨站源 403）；轮换开启时新 refresh 必须随响应重下 Cookie；
- 登出无条件清理三条 Cookie；refresh 在 Cookie 里时同样拉黑。
"""

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/identity/login/basic"
REFRESH_URL = "/api/identity/refresh"
LOGOUT_URL = "/api/identity/logout"
USERINFO_URL = "/api/identity/userinfo"

ACCESS_COOKIE = "X-Token"
REFRESH_COOKIE = "X-Refresh-Token"
MODE_COOKIE = "X-Auth-Mode"


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token（与 test_auth_api 同口径）。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _login(api_client):
    return api_client.post(LOGIN_URL, {"username": "zhangsan", "password": "Test@123456"}, format="json")


def _login_tokens(api_client):
    """登录后取 (access, refresh)：Header 模式读响应体，Cookie 模式读 Set-Cookie 值。"""
    resp = _login(api_client)
    data = resp.data["data"]
    if data["access"]:
        return data["access"], data["refresh"]
    return resp.cookies[ACCESS_COOKIE].value, resp.cookies[REFRESH_COOKIE].value


def _cookie_client(pairs):
    """独立客户端：只带指定 Cookie（不带 api_client 附带的 User-Agent 之外的状态）。"""
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    for name, value in pairs.items():
        client.cookies[name] = value
    return client


class TestIssuance:
    """开关两态的登录签发形态。"""

    def test_header_mode_default_unchanged(self, api_client, normal_user, login_free):
        """默认：token 在响应体；X-Token/X-Refresh-Token 不经服务端下发；标记 = header。"""
        resp = _login(api_client)
        assert resp.status_code == 200
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]
        assert ACCESS_COOKIE not in resp.cookies
        assert REFRESH_COOKIE not in resp.cookies
        assert resp.cookies[MODE_COOKIE].value == "header"

    def test_cookie_mode_sets_httponly_cookies_and_strips_body(self, api_client, normal_user, login_free, settings):
        """开启：HttpOnly Cookie 下发 + 响应体剥离（键保留）。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        resp = _login(api_client)
        assert resp.status_code == 200
        access_cookie = resp.cookies[ACCESS_COOKIE]
        refresh_cookie = resp.cookies[REFRESH_COOKIE]
        assert access_cookie["httponly"]
        assert refresh_cookie["httponly"]
        assert access_cookie["samesite"] == "Lax"
        assert refresh_cookie["samesite"] == "Lax"
        # Cookie 寿命 = token 生命周期 - 10（与前端 setToken 口径一致）
        assert access_cookie["max-age"] == resp.data["data"]["access_token_lifetime"] - 10
        assert refresh_cookie["max-age"] == resp.data["data"]["refresh_token_lifetime"] - 10
        # 响应体：键保留、值剥离（前端登录成功断言不受影响）
        assert "access" in resp.data["data"] and resp.data["data"]["access"] == ""
        assert "refresh" in resp.data["data"] and resp.data["data"]["refresh"] == ""
        # 模式标记 JS 可读（无 httponly）
        assert resp.cookies[MODE_COOKIE].value == "cookie"
        assert not resp.cookies[MODE_COOKIE]["httponly"]
        # HTTP 请求不带 Secure（Secure Cookie 在非安全上下文会被浏览器静默丢弃）
        assert not access_cookie["secure"]

    def test_cookie_mode_secure_request_adds_secure_flag(self, api_client, normal_user, login_free, settings):
        """HTTPS 请求（is_secure）：Cookie 附带 Secure 属性。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        resp = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
            secure=True,
        )
        assert resp.cookies[ACCESS_COOKIE]["secure"]
        assert resp.cookies[REFRESH_COOKIE]["secure"]

    def test_mode_marker_issued_in_both_modes(self, api_client, normal_user, login_free, settings):
        """标记 Cookie 两种模式都下发：开关回退后旧标记不残留误导新会话。"""
        assert _login(api_client).cookies[MODE_COOKIE].value == "header"
        settings.AUTH_COOKIE_HTTPONLY = True
        assert _login(api_client).cookies[MODE_COOKIE].value == "cookie"


class TestRefreshDualRead:
    """刷新端点 body / Cookie 双读 + Cookie 通道 CSRF。"""

    @staticmethod
    def _login_pair(api_client):
        return _login_tokens(api_client)

    def test_refresh_via_cookie_rotates_and_reissues_cookies(self, api_client, normal_user, login_free, settings):
        """Cookie 模式：空 body + X-Refresh-Token Cookie → 刷新成功，新 Cookie 随响应重下。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        _, refresh = self._login_pair(api_client)
        resp = _cookie_client({REFRESH_COOKIE: refresh}).post(REFRESH_URL, {}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["access"] == ""
        assert resp.cookies[ACCESS_COOKIE]["httponly"]
        # 轮换开启：新 refresh 与旧值不同，且必须重下（旧 refresh 已被拉黑）
        new_refresh = resp.cookies[REFRESH_COOKIE].value
        assert new_refresh != refresh
        # 旧 refresh 已拉黑：再用旧 Cookie 刷新 → 401
        stale = _cookie_client({REFRESH_COOKIE: refresh})
        assert stale.post(REFRESH_URL, {}, format="json").status_code == 401
        # 新 refresh Cookie 可继续刷新
        nxt = _cookie_client({REFRESH_COOKIE: new_refresh})
        assert nxt.post(REFRESH_URL, {}, format="json").status_code == 200

    def test_refresh_cross_site_origin_rejected(self, api_client, normal_user, login_free, settings):
        """Cookie 凭证 + 跨站 Origin：403（Cookie 是环境凭证，须过 Origin 兜底）。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        _, refresh = self._login_pair(api_client)
        client = _cookie_client({REFRESH_COOKIE: refresh})
        resp = client.post(REFRESH_URL, {}, format="json", HTTP_ORIGIN="http://evil.example")
        assert resp.status_code == 403

    def test_refresh_same_origin_allowed(self, api_client, normal_user, login_free, settings):
        """同源 Origin 放行（浏览器同源 POST 必带 Origin，不受影响）。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        _, refresh = self._login_pair(api_client)
        client = _cookie_client({REFRESH_COOKIE: refresh})
        resp = client.post(REFRESH_URL, {}, format="json", HTTP_ORIGIN="http://testserver")
        assert resp.status_code == 200

    def test_refresh_body_takes_precedence_over_cookie(self, api_client, normal_user, login_free):
        """body 有 refresh 时优先（Header 模式兼容）；Cookie 不参与、不做 Origin 校验。"""
        _, refresh = self._login_pair(api_client)
        client = _cookie_client({REFRESH_COOKIE: "garbage-cookie-value"})
        resp = client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert resp.status_code == 200
        assert resp.data["data"]["access"]

    def test_refresh_with_neither_body_nor_cookie_rejected(self, api_client, normal_user, login_free):
        """双缺失：按 simplejwt 序列化器校验失败（400），不 500。"""
        assert api_client.post(REFRESH_URL, {}, format="json").status_code == 400

    def test_invalid_access_cookie_does_not_block_refresh(self, api_client, normal_user, login_free, settings):
        """X-Token Cookie 无效/过期时不阻断刷新：HttpOnly access 前端删不掉，
        刷新端点是空认证链（simplejwt TokenViewBase），认证不跑、凭证只认 refresh。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        _, refresh = self._login_pair(api_client)
        client = _cookie_client({ACCESS_COOKIE: "not-a-jwt", REFRESH_COOKIE: refresh})
        assert client.post(REFRESH_URL, {}, format="json").status_code == 200


class TestLogoutCookieCleanup:
    """登出：Cookie 拉黑 + 三条 Cookie 清理（无条件，不随开关）。"""

    @staticmethod
    def _cookie_session(api_client):
        access, refresh = TestRefreshDualRead._login_pair(api_client)
        return _cookie_client({ACCESS_COOKIE: access, REFRESH_COOKIE: refresh}), access, refresh

    def test_logout_blacklists_cookie_refresh_and_clears_cookies(self, api_client, normal_user, login_free, settings):
        """Cookie 模式登出：refresh 从 Cookie 拉黑，三条 Cookie 全部清空下发。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        client, access, refresh = self._cookie_session(api_client)
        resp = client.post(LOGOUT_URL, {}, format="json")
        assert resp.status_code == 200
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, MODE_COOKIE):
            assert name in resp.cookies, f"{name} 应在登出响应中清理"
            assert resp.cookies[name].value == ""
            assert resp.cookies[name]["max-age"] == 0
        # 旧 access 已拉黑：携带它访问 userinfo → 401
        assert _cookie_client({ACCESS_COOKIE: access}).get(USERINFO_URL).status_code == 401
        # 旧 refresh 已拉黑：登出后不可再续期
        assert _cookie_client({REFRESH_COOKIE: refresh}).post(REFRESH_URL, {}, format="json").status_code == 401

    def test_logout_header_mode_also_clears_server_cookies(self, api_client, normal_user, login_free):
        """开关关闭时登出同样下发清理头：回退窗口内新旧会话形态都登出干净。"""
        client, _, _ = self._cookie_session(api_client)
        resp = client.post(LOGOUT_URL, {}, format="json")
        assert resp.status_code == 200
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, MODE_COOKIE):
            assert name in resp.cookies


class TestAuthDualReadHeaderCompat:
    """Header（Bearer/PAT）通道不受开关影响。"""

    def test_bearer_header_still_works_in_cookie_mode(self, api_client, normal_user, login_free, settings):
        """开关开启后 Authorization 头照常认证（服务端双读，脚本 / PAT 不受影响）。"""
        settings.AUTH_COOKIE_HTTPONLY = True
        # Cookie 模式登录响应体已剥离 token：直接签发（模拟脚本/集成方持 token 走头）
        access = str(RefreshToken.for_user(normal_user).access_token)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        assert client.get(USERINFO_URL, HTTP_AUTHORIZATION=f"Bearer {access}").status_code == 200
