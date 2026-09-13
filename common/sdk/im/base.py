#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IM 客户端基类：token 缓存、响应判定、http 注入的公共件。"""

import hashlib
from urllib.parse import urlencode

from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)

# token 提前失效秒数（三家有效期均为 2 小时量级）
TOKEN_TTL_SLACK = 120


class ImSdkError(Exception):
    """渠道发送失败（含渠道侧拒绝）。message 面向日志，不直接回显给终端用户。"""

    def __init__(self, message, code=None):
        self.code = code
        super().__init__(message)


class BaseImClient:
    """凭据由调用方注入（settings 读出的三元组 dict），便于测试与多实例。"""

    token_url = ""
    token_ttl = 7200
    # 缓存 key 前缀（子类覆盖，如 "im_dingtalk_token_"）
    token_cache_prefix = ""

    def __init__(self, credentials: dict = None, http_client=None):
        # 凭据留存实例：token 缓存 key 由凭据摘要派生（改密即换 key，不沿用旧 token）
        self.credentials = credentials or {}
        self.http = http_client

    # ---------------------------------------------------------------- http

    def _client(self):
        if self.http is None:
            import requests

            self.http = requests
        return self.http

    def _get_json(self, url, params=None, headers=None, timeout=10):
        try:
            response = self._client().get(url, params=params or {}, headers=headers or {}, timeout=timeout)
            payload = response.json()
        except Exception as exc:
            raise ImSdkError(f"request failed: {exc}") from exc
        return self._check(payload, url) if isinstance(payload, dict) else {}

    def _post_json(self, url, body, params=None, headers=None, timeout=10):
        try:
            response = self._client().post(url, json=body, params=params or {}, headers=headers or {}, timeout=timeout)
            payload = response.json()
        except Exception as exc:
            raise ImSdkError(f"request failed: {exc}") from exc
        return self._check(payload, url) if isinstance(payload, dict) else {}

    def _post_query(self, url, params, timeout=10):
        """钉钉旧版 oapi 风格：access_token 走 query string 的 POST。"""
        return self._post_json(f"{url}?{urlencode(params)}", {}, timeout=timeout)

    def _check(self, payload, url):
        """渠道侧错误判定：子类按各家语义覆盖（返回 payload 或抛 ImSdkError）。"""
        return payload

    # ---------------------------------------------------------------- token

    def _credentials_digest(self, credentials: dict) -> str:
        """凭据摘要（键排序 + 带键名）：与 dict 构造顺序解耦，不同渠道/不同凭据互不命中。"""
        raw = ":".join(f"{key}={value or ''}" for key, value in sorted((credentials or {}).items()))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _cached_token(self) -> str:
        """租户 token（按实例凭据缓存）：凭据变化自动换 key，避免用旧 token 掩盖配置错误。"""
        cache_key = self.token_cache_prefix + self._credentials_digest(self.credentials)
        token = cache.get(cache_key)
        if token:
            return str(token)
        token = self._fetch_token(self.credentials)
        cache.set(cache_key, token, max(self.token_ttl - TOKEN_TTL_SLACK, 60))
        return token

    def _fetch_token(self, credentials: dict) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------ 发送

    def send_text(self, accounts, content) -> None:
        raise NotImplementedError
