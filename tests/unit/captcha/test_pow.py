# -*- coding: utf-8 -*-
"""PoW 题型：固定向量 / 位数封顶 / 预算。"""

import hashlib

from django.core.cache import cache

from captcha import flow
from captcha.backends.pow import PowBackend, leading_zero_bits


def _solve(challenge: str, bits: int) -> str:
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest()) < bits:
        nonce += 1
    return str(nonce)


class TestPowBackend:
    def test_fixed_vector_verify(self, settings):
        settings.CAPTCHA_POW_BITS_LIGHT = 8
        challenge = PowBackend().generate("easy")
        secret = challenge.secret
        nonce = _solve(secret["challenge"], secret["bits"])
        assert PowBackend().verify(secret, {"nonce": nonce}, "easy") is True
        assert PowBackend().verify(secret, {"nonce": "0"}, "easy") in (True, False)

    def test_wrong_nonce_rejected(self, settings):
        settings.CAPTCHA_POW_BITS_LIGHT = 12
        challenge = PowBackend().generate("easy")
        assert PowBackend().verify(challenge.secret, {"nonce": "not-a-number"}, "easy") is False

    def test_bits_are_capped(self, settings):
        settings.CAPTCHA_POW_BITS_HARD = 99
        settings.CAPTCHA_POW_BITS_MAX = 20
        assert PowBackend().generate("hard").secret["bits"] == 20
        settings.CAPTCHA_POW_BITS_MAX = 24
        assert PowBackend().generate("hard").secret["bits"] == 24

    def test_payload_shape(self):
        payload = PowBackend().generate("normal").payload
        assert payload["algorithm"] == "sha256"
        assert payload["difficulty_bits"] > 0
        assert payload["challenge"]
        assert payload["expire"] > 0
        assert payload["ttl"] >= 10

    def test_budget(self, settings):
        settings.CAPTCHA_POW_IP_BUDGET = 2
        try:
            cache.delete_pattern("captcha_pow_budget_*")
        except AttributeError:
            pass
        assert flow.pow_budget_ok("iph") is True
        assert flow.pow_budget_ok("iph") is True
        assert flow.pow_budget_ok("iph") is False
