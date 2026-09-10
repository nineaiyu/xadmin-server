#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : throttle
# author : ly_13
# date : 6/2/2023


from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle, AnonRateThrottle


class RegisterThrottle(AnonRateThrottle):
    scope = "register"


class ResetPasswordThrottle(AnonRateThrottle):
    scope = "reset_password"


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class UploadThrottle(UserRateThrottle):
    """上传速率限制"""

    scope = "upload"


class Download1Throttle(UserRateThrottle):
    """下载速率限制"""

    scope = "download1"


class Download2Throttle(UserRateThrottle):
    """下载速率限制"""

    scope = "download2"


class PatThrottle(SimpleRateThrottle):
    """PAT 凭证级限流：按 token_hash 计数（PAT_RATE_LIMIT，空/0 = 不限）。

    全局挂载（DEFAULT_THROTTLE_CLASSES）：非 PAT 请求 get_cache_key 返回 None
    直接放行，仅对 PAT 认证生效；速率走系统配置（区别于静态 THROTTLE_RATES）。
    """

    scope = "pat"

    def get_rate(self):
        # 覆盖默认实现：速率来自 SysConfig 动态配置而非静态 THROTTLE_RATES；
        # 空 / "0"（数字 0 同）= 不限（直接传 parse_rate 会因缺单位 ValueError）
        from common.core.config import SysConfig

        limit = str(SysConfig.PAT_RATE_LIMIT or "").strip()
        return None if not limit or limit == "0" else limit

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        from django.apps import apps

        pat_model = apps.get_model("system", "PersonalAccessToken")
        auth = getattr(request, "auth", None)
        if auth is not None and isinstance(auth, pat_model):
            return self.cache_format % {"scope": self.scope, "ident": auth.token_hash}
        return None
