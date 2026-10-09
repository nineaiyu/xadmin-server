# -*- coding: utf-8 -*-
"""common.utils.token 缓存 token 单元测试（基于 FakeRedis 默认缓存）。"""

import pytest

from common.utils.token import make_token_cache, verify_token_cache

pytestmark = pytest.mark.django_db


class TestTokenCache:
    def test_make_and_verify_token(self):
        token = make_token_cache("security-bind")
        values = verify_token_cache(token, "security-bind")
        assert values
        assert values["data"] == "security-bind"

    def test_verify_wrong_key(self):
        token = make_token_cache("hello")
        assert verify_token_cache(token, "world") is False

    def test_verify_nonexistent_token(self):
        assert verify_token_cache("tmp_token_not_exist", "x") is False

    def test_success_once_consumes_token(self):
        token = make_token_cache("once")
        assert verify_token_cache(token, "once", success_once=True)
        # 一次性消费：再次校验因缓存已删除而失败
        assert verify_token_cache(token, "once", success_once=True) is False

    def test_same_key_returns_same_token_without_force_new(self):
        token1 = make_token_cache("same-key")
        token2 = make_token_cache("same-key")
        assert token1 == token2

    def test_force_new_generates_new_token(self):
        token1 = make_token_cache("force-key", force_new=True)
        token2 = make_token_cache("force-key", force_new=True)
        assert token1 != token2


class TestTokenCacheKeyDigest:
    """缓存键用令牌摘要而非原文：键会出现在 Redis 键空间 / 慢查询日志 / 监控面板里。"""

    def test_cache_key_contains_no_token_plaintext(self):
        from django.core.cache import cache

        from common.utils.token import TEMP_TOKEN_CACHE_PREFIX, temp_token_cache_key

        token = make_token_cache("digest-key")
        cache_key = temp_token_cache_key(token)
        assert cache_key.startswith(TEMP_TOKEN_CACHE_PREFIX)
        assert token not in cache_key
        assert temp_token_cache_key(token) == cache_key  # 幂等

        assert cache.get(cache_key) is not None
        assert cache.get(token) is None  # 原文不再落键

    def test_plaintext_key_migrated_on_verify(self):
        """升级前签发的令牌（原文作键）命中后迁移到摘要键并清理旧键。"""
        import time

        from django.core.cache import cache

        from common.utils.token import RedisCacheBase, temp_token_cache_key

        legacy_token = "tmp_token_legacy_payload"
        RedisCacheBase(legacy_token).set_storage_cache(
            {"atime": time.time() + 300, "data": "legacy-key", "ext_data": None}, 300
        )
        values = verify_token_cache(legacy_token, "legacy-key")
        assert values and values["data"] == "legacy-key"
        assert cache.get(temp_token_cache_key(legacy_token)) is not None
        assert cache.get(legacy_token) is None
        # 再次校验走摘要键，仍可用
        assert verify_token_cache(legacy_token, "legacy-key")
