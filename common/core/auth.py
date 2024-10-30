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
from rest_framework.exceptions import NotAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from common.cache.storage import BlackAccessTokenCache


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
        user_id = self.payload.get('user_id')
        if BlackAccessTokenCache(user_id, hashlib.md5(self.token).hexdigest()).get_storage_cache():
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
            cookies = request.META.get('HTTP_COOKIE')
            if cookies:
                cookie_dict = parse_cookie(cookies)
                if cookie_dict and cookie_dict.get('X-Token'):
                    header = f"Bearer {cookie_dict.get('X-Token')}".encode('utf-8')
        return header
