#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""PoW 题型（算力轴）：浏览器自增 nonce 求 SHA-256 前缀零比特。

服务端校验只做一次哈希（O(1)），成本几乎不随难度上升；位数由决策档位下发
并封顶 ``CAPTCHA_POW_BITS_MAX``（默认 20），避免把服务端变成算力放大器。
"""

import hashlib
import secrets
import time
from typing import Any

from django.conf import settings

from captcha.backends.base import CaptchaChallenge, ChallengeBackend
from captcha.const import CAPTCHA_DIFFICULTY_EASY, CAPTCHA_DIFFICULTY_HARD


def bits_for(difficulty: str = "") -> int:
    hard = int(getattr(settings, "CAPTCHA_POW_BITS_HARD", 20) or 20)
    light = int(getattr(settings, "CAPTCHA_POW_BITS_LIGHT", 18) or 18)
    allow = int(getattr(settings, "CAPTCHA_POW_BITS_ALLOW", 16) or 16)
    cap = int(getattr(settings, "CAPTCHA_POW_BITS_MAX", 20) or 20)
    if difficulty == CAPTCHA_DIFFICULTY_HARD:
        value = hard
    elif difficulty == CAPTCHA_DIFFICULTY_EASY:
        value = allow
    else:
        value = light
    return max(1, min(value, cap, 24))


def leading_zero_bits(hex_digest: str) -> int:
    total = 0
    for char in hex_digest:
        value = int(char, 16)
        if value == 0:
            total += 4
            continue
        total += 4 - value.bit_length()
        break
    return total


class PowBackend(ChallengeBackend):
    type = "pow"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        bits = bits_for(difficulty)
        challenge = secrets.token_hex(16)
        ttl = int(getattr(settings, "CAPTCHA_CHALLENGE_TTL", 120) or 120)
        return CaptchaChallenge(
            type=self.type,
            secret={"challenge": challenge, "bits": bits},
            payload={
                "algorithm": "sha256",
                "challenge": challenge,
                "difficulty_bits": bits,
                "expire": int(time.time()) + ttl,
            },
        )

    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        nonce = answer.get("nonce") if isinstance(answer, dict) else answer
        if isinstance(nonce, bool) or nonce is None:
            return False
        try:
            nonce_text = str(int(nonce))
        except (TypeError, ValueError):
            return False
        challenge = str(secret.get("challenge") or "")
        bits = int(secret.get("bits") or 0)
        if not challenge or bits <= 0:
            return False
        digest = hashlib.sha256(f"{challenge}{nonce_text}".encode()).hexdigest()
        return leading_zero_bits(digest) >= bits
