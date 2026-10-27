#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""图片字符验证码题型（统一协议）。

图片码不再使用 DB ``CaptchaStore`` / ``captcha_key``：题目文本与答案只进
Redis 挑战密文，校验通过后签发与其他题型一致的 ticket。默认权重 0，
只作为无障碍回退或显式请求题型使用。
"""

import secrets
from pathlib import Path
from typing import Any

from django.conf import settings

from captcha.backends.art import png_data_uri
from captcha.backends.base import CaptchaChallenge, ChallengeBackend
from captcha.backends.pow import PowBackend
from captcha.const import CAPTCHA_DIFFICULTY_EASY
from captcha.helpers import get_challenge
from captcha.rendering import render_challenge


def _audio_text(challenge: str) -> str:
    if settings.CAPTCHA_CHALLENGE_FUNCT == "captcha.helpers.math_challenge":
        return challenge.replace("*", " times ").replace("-", " minus ").replace("+", " plus ")
    return ", ".join(list(challenge))


class ImageBackend(ChallengeBackend):
    type = "image"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        challenge, response = get_challenge()()
        image = render_challenge(challenge, 1)
        answer = str(response).strip().lower()
        secret: dict[str, Any] = {
            "answer": answer,
            "audio_text": _audio_text(challenge),
            "asset_id": "builtin-image",
        }
        payload: dict[str, Any] = {
            "background": png_data_uri(image),
            "width": image.width,
            "height": image.height,
            "length": len(answer),
            "audio": Path(str(getattr(settings, "CAPTCHA_FLITE_PATH", "") or "")).is_file(),
        }
        if bool(getattr(settings, "CAPTCHA_POW_FOR_IMAGE_FALLBACK", True)):
            proof = PowBackend().generate(CAPTCHA_DIFFICULTY_EASY)
            secret["pow"] = proof.secret
            payload["pow"] = proof.payload
        return CaptchaChallenge(type=self.type, secret=secret, payload=payload)

    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        if isinstance(answer, dict) and secret.get("pow"):
            if not PowBackend().verify(secret["pow"], answer.get("pow_answer")):
                return False
        value = answer.get("code") if isinstance(answer, dict) else answer
        if not isinstance(value, str):
            return False
        expected = str(secret.get("answer") or "")
        return bool(expected) and secrets.compare_digest(value.strip().lower(), expected)
