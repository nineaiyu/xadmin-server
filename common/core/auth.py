#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : system
# author : ly_13
# date : 6/2/2023
import functools
import hashlib

from django.http.cookie import parse_cookie
from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache, UserTokenRevokedCache


def auth_required(view_func):
    @functools.wraps(view_func)
    def wrapper(view, request, *args, **kwargs):
        if request.user and request.user.is_authenticated:
            return view_func(view, request, *args, **kwargs)
        raise NotAuthenticated(_("Unauthorized authentication"))

    return wrapper


class ServerAccessToken(AccessToken):
    """
    自定义的token方法是为了登出的时候，将 access token 禁用
    """

    def verify(self):
        user_id = self.payload.get("user_id")
        # token 在认证链路为 bytes，兼容 str 入参（测试/工具直调）
        raw_token = self.token if isinstance(self.token, bytes) else str(self.token).encode()
        if BlackAccessTokenCache(user_id, hashlib.md5(raw_token).hexdigest()).get_storage_cache():
            raise TokenError(_("Token is invalid or expired"))
        # 强制下线（踢全部会话）：服务端拿不到用户的 token 清单，用「失效时间戳 +
        # iat 比较」拒绝被踢时刻之前签发的所有 access token
        revoked_at = UserTokenRevokedCache(user_id).get_storage_cache()
        if revoked_at and float(self.payload.get("iat", 0)) <= float(revoked_at):
            raise TokenError(_("Token is invalid or expired"))
        # 单会话下线（在线用户页行维度）：按登录时写入的 sid claim 精确拒绝，
        # 不影响该用户其他在用登录；旧 token 无 sid 自然跳过
        sid = self.payload.get("sid")
        if sid and SessionTokenRevokedCache(sid).get_storage_cache():
            raise TokenError(_("Token is invalid or expired"))
        super().verify()


class GetUserFromAccessToken(AccessToken):
    token_type = "refresh"


class CookieJWTAuthentication(JWTAuthentication):
    """
    支持cookie认证，是为了可以访问 django-proxy 的页面，比如 flower
    """

    def get_header(self, request):
        header = super().get_header(request)
        if not header:
            cookies = request.META.get("HTTP_COOKIE")
            if cookies:
                cookie_dict = parse_cookie(cookies)
                if cookie_dict and cookie_dict.get("X-Token"):
                    header = f"Bearer {cookie_dict.get('X-Token')}".encode("utf-8")
        return header

    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            # 会话活跃刷新（登录即登记的 UserSession）：节流门控在 touch 内部，
            # 失败静默——会话管理属附加能力，不能影响认证主链路
            try:
                sid = result[1].payload.get("sid")
                if sid:
                    from django.apps import apps

                    apps.get_model("system", "UserSession").touch(sid)
            except Exception:  # noqa: BLE001 会话刷新失败不影响认证
                pass
        return result


class PersonalAccessTokenAuthentication(BaseAuthentication):
    """个人访问令牌（PAT）认证：`Authorization: Pat <token>`。

    - 与 JWT 认证链并列（DEFAULT_AUTHENTICATION_CLASSES 末位）：无 PAT 头时静默
      返回 None，完全不影响既有认证方式；带了 PAT 头但无效则显式 401（fail-closed）。
    - PAT 完全继承所属用户（creator）既有权限：三层权限/数据权限/审计全链路天然生效，
      不能越过用户权限。
    - 不建 UserSession、不受 UserTokenRevokedCache 影响（按 iat 只针对 JWT）；
      吊销 = 置 is_active=False，每次认证查库即时生效。
    - last_used_time 经 Redis 60s 节流更新，防止高频机器请求刷库。
    """

    keyword = "pat"
    LAST_USED_THROTTLE_SECONDS = 60

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword:
            return None

        from django.apps import apps
        from django.core.cache import cache
        from django.utils import timezone

        token_model = apps.get_model("system", "PersonalAccessToken")
        pat = (
            token_model.objects.filter(token_hash=self.hash_token(parts[1]), is_active=True)
            .select_related("creator")
            .first()
        )
        if pat is None:
            raise AuthenticationFailed(_("Token is invalid or expired"))
        now = timezone.now()
        if pat.expired_at and pat.expired_at <= now:
            raise AuthenticationFailed(_("Token is invalid or expired"))
        user = pat.creator
        if user is None or not user.is_active:
            raise AuthenticationFailed(_("User account is disabled"))

        # last_used_time 节流更新：cache.add 原子占位，60s 内多次请求只回写一次
        throttle_key = f"pat_last_used_{pat.pk}"
        if cache.add(throttle_key, 1, self.LAST_USED_THROTTLE_SECONDS):
            token_model.objects.filter(pk=pat.pk).update(last_used_time=now)
        return (user, pat)
