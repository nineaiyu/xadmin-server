# -*- coding: utf-8 -*-
"""图片题型：统一协议下的生成 / 校验 / PoW 证明 / 音频文本。"""

import base64
import hashlib
from io import BytesIO

from PIL import Image

from captcha.backends.image import ImageBackend
from captcha.backends.pow import leading_zero_bits


def _solve(pow_payload) -> str:
    challenge = str(pow_payload["challenge"])
    bits = int(pow_payload["difficulty_bits"])
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest()) < bits:
        nonce += 1
    return str(nonce)


def _open_data_uri(uri: str) -> Image.Image:
    assert uri.startswith("data:image/png;base64,")
    return Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1])))


class TestImageBackend:
    def test_generate_payload_has_no_answer_and_valid_png(self, settings):
        settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = True
        settings.CAPTCHA_POW_BITS_ALLOW = 4
        generated = ImageBackend().generate()
        assert generated.type == "image"
        assert "answer" not in generated.payload
        assert generated.payload["length"] > 0
        assert generated.payload["audio"] in (True, False)
        assert generated.payload["pow"]["difficulty_bits"] == 4
        image = _open_data_uri(generated.payload["background"])
        assert image.width == generated.payload["width"]
        assert image.height == generated.payload["height"]
        assert generated.secret["audio_text"]
        assert generated.secret["asset_id"] == "builtin-image"

    def test_verify_requires_code_and_pow(self, settings):
        settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = True
        settings.CAPTCHA_POW_BITS_ALLOW = 4
        generated = ImageBackend().generate()
        answer = generated.secret["answer"]
        nonce = _solve(generated.payload["pow"])
        assert ImageBackend().verify(generated.secret, {"code": answer, "pow_answer": {"nonce": nonce}}) is True
        assert ImageBackend().verify(generated.secret, {"code": "wrong", "pow_answer": {"nonce": nonce}}) is False
        assert ImageBackend().verify(generated.secret, {"code": answer, "pow_answer": {"nonce": "bad"}}) is False
        assert ImageBackend().verify(generated.secret, {"code": answer}) is False
        # 裸字符串答案不得绕过内嵌 PoW
        assert ImageBackend().verify(generated.secret, answer) is False

    def test_pow_can_be_disabled(self, settings):
        settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = False
        generated = ImageBackend().generate()
        assert "pow" not in generated.payload
        assert ImageBackend().verify(generated.secret, {"code": generated.secret["answer"]}) is True
