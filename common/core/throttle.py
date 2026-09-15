#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : throttle
# author : ly_13
# date : 6/2/2023


from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle


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
    直接放行，仅对 PAT 认证生效；速率按认证用户动态取值——个人行优先，
    未设置/未启用继承时回退系统级（区别于静态 THROTTLE_RATES）。
    """

    scope = "pat"

    def get_rate(self, request=None):
        # 覆盖默认实现：速率来自 SysConfig/UserConfig 动态配置而非静态 THROTTLE_RATES；
        # 空 / "0"（数字 0 同）= 不限（直接传 parse_rate 会因缺单位 ValueError）
        from common.core.config import SysConfig, UserConfig

        if request is not None:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                # 用户级值已内含继承链（无个人行时即系统值），非空即权威，
                # 个人行 "0"（不限）不会被系统级值覆盖
                limit = UserConfig(user).PAT_RATE_LIMIT
                if isinstance(limit, str) and limit.strip():
                    limit = limit.strip()
                    return None if limit == "0" else limit
        limit = str(SysConfig.PAT_RATE_LIMIT or "").strip()
        return None if not limit or limit == "0" else limit

    def allow_request(self, request, view):
        # 实例化早于认证（拿不到 request.user），PAT 请求在此按认证用户重取速率；
        # 非 PAT 请求维持实例化时的系统级判定，不额外读用户配置。
        # rate 变更须同步重算 num_requests/duration（DRF 在实例化期解析一次）
        if self.get_cache_key(request, view) is not None:
            self.rate = self.get_rate(request)
            self.num_requests, self.duration = self.parse_rate(self.rate)
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
