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