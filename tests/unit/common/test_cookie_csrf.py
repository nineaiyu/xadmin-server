# -*- coding: utf-8 -*-
"""Cookie 认证写请求的服务端 CSRF 兜底：跨站源拒绝、同源放行、无头按配置三态。

口径（与 Django CsrfViewMiddleware 的安全方法集一致）：

- 只有 **X-Token Cookie 通道 + 非安全方法** 才校验；Bearer / PAT 头凭证不经此分支；
- ``Origin``（优先）或 ``Referer`` 的源须命中同源或 ``CSRF_TRUSTED_ORIGINS``；
- 两者都缺失时按 ``CSRF_REQUIRE_ORIGIN``（默认放行，CLI/脚本兼容）。
"""

import pytest
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from common.core.auth import CookieJWTAuthentication

pytestmark = pytest.mark.django_db

AUTH_URL = "/api/identity/userinfo"


def _cookie_request(method, token, **headers):
    """构造带 X-Token Cookie 的裸请求（认证类只消费 META，与既有测试同口径）。"""
    factory = APIRequestFactory()
    request = getattr(factory, method)(AUTH_URL)
    request.META["HTTP_COOKIE"] = f"X-Token={token}"
    for key, value in headers.items():
        request.META[f"HTTP_{key.upper()}"] = value
    return request


def _valid_token(user):
    return str(RefreshToken.for_user(user).access_token)


class TestCookieChannelCsrf:
    def test_cross_site_origin_rejected(self, superuser):
        """Cookie 通道 + 跨站 Origin：拒绝（认证前即拦截）。"""
        request = _cookie_request("post", _valid_token(superuser), origin="http://evil.example")
        with pytest.raises(PermissionDenied):
            CookieJWTAuthentication().authenticate(request)

    def test_cross_site_referer_rejected(self, superuser):
        """无 Origin 但 Referer 指向跨站源：同样拒绝。"""
        request = _cookie_request("post", _valid_token(superuser), referer="http://evil.example/profile")
        with pytest.raises(PermissionDenied):
            CookieJWTAuthentication().authenticate(request)

    def test_same_origin_origin_header_allowed(self, superuser):
        """同源 Origin（host 命中 request.get_host）：放行并完成认证。"""
        request = _cookie_request("post", _valid_token(superuser), origin="http://testserver")
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

    def test_same_host_cross_scheme_origin_allowed(self, superuser):
        """TLS 终结于反代的部署：scheme 不参与同源判定（host 一致即同源）。"""
        request = _cookie_request("post", _valid_token(superuser), origin="https://testserver")
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

    def test_trusted_origins_whitelist(self, superuser, settings):
        """CSRF_TRUSTED_ORIGINS 精确条目：白名单源放行、相似域不放行。"""
        settings.CSRF_TRUSTED_ORIGINS = ["https://partner.example.com"]
        request = _cookie_request("post", _valid_token(superuser), origin="https://partner.example.com")
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

        request = _cookie_request("post", _valid_token(superuser), origin="https://partner.example.com.evil.net")
        with pytest.raises(PermissionDenied):
            CookieJWTAuthentication().authenticate(request)

    def test_trusted_origins_wildcard_subdomain(self, superuser, settings):
        """``https://*.example.com`` 形态：子域放行、异 scheme 子域不放行（同 Django 口径）。"""
        settings.CSRF_TRUSTED_ORIGINS = ["https://*.example.com"]
        request = _cookie_request("post", _valid_token(superuser), origin="https://app.example.com")
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

        request = _cookie_request("post", _valid_token(superuser), origin="http://app.example.com")
        with pytest.raises(PermissionDenied):
            CookieJWTAuthentication().authenticate(request)

    def test_missing_headers_allowed_by_default(self, superuser):
        """Origin/Referer 双缺失 + 默认配置：放行（CLI / 脚本直调兼容口径）。"""
        request = _cookie_request("post", _valid_token(superuser))
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

    def test_missing_headers_rejected_when_required(self, superuser, settings):
        """CSRF_REQUIRE_ORIGIN=true：双缺失即拒绝（高危部署收紧项）。"""
        settings.CSRF_REQUIRE_ORIGIN = True
        request = _cookie_request("post", _valid_token(superuser))
        with pytest.raises(PermissionDenied):
            CookieJWTAuthentication().authenticate(request)

    def test_safe_methods_skip_csrf_check(self, superuser):
        """Cookie 通道的 GET（媒体 / 图片请求主形态）：不做 CSRF 校验。"""
        request = _cookie_request("get", _valid_token(superuser), origin="http://evil.example")
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

    def test_bearer_header_path_unaffected(self, superuser):
        """Bearer 头凭证不受 CSRF 兜底影响：跨站 Origin 照常认证（头凭证不可被跨站携带）。"""
        token = _valid_token(superuser)
        request = APIRequestFactory().post(
            AUTH_URL, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_ORIGIN="http://evil.example"
        )
        user, _token = CookieJWTAuthentication().authenticate(request)
        assert user.pk == superuser.pk

    def test_invalid_token_still_rejected_after_csrf_pass(self, superuser):
        """CSRF 放行不放松认证：无效 token 照常 401。"""
        request = _cookie_request("post", "not-a-jwt", origin="http://testserver")
        with pytest.raises(AuthenticationFailed):
            CookieJWTAuthentication().authenticate(request)
