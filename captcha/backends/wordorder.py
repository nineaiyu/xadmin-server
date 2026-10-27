#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""语序点选题型：按提示语序依次点击图中文字（抗多模态的顺序约束）。

复用文字点选的绘制 / 命中基座；提示条也是图片，顺序规则按短词表轮换。
答案仍是 ``points`` 有序数组，命中半径随难度缩放。
"""

import random
from typing import Any

from django.conf import settings
from PIL import Image, ImageFont

from captcha.backends.art import make_background_asset, png_data_uri, size_setting
from captcha.backends.base import CaptchaChallenge
from captcha.backends.wordclick import (
    WordClickBackend,
    _font_path,
    _render_glyph,
    _render_tip,
    _sample_positions,
    selected_charset,
)

PHRASES = {
    "easy": ("春江花", "秋月夜", "山川美", "风林火"),
    "normal": ("春夏秋冬", "风花雪月", "东西南北", "金木水火", "琴棋书画", "梅兰竹菊"),
    "hard": ("日月星辰海", "山河湖海风", "金木水火土", "东西南北中"),
}
TRADITIONAL_PHRASES = {
    "easy": ("春江花", "秋月夜", "山川美", "風林火"),
    "normal": ("春夏秋冬", "風花雪月", "東西南北", "金木水火", "琴棋書畫", "梅蘭竹菊"),
    "hard": ("日月星辰海", "山河湖海風", "金木水火土", "東西南北中"),
}


class WordOrderBackend(WordClickBackend):
    type = "wordorder"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        rng = random.Random()
        width, height = size_setting(getattr(settings, "CAPTCHA_SLIDE_SIZE", None), (320, 180))
        tier = difficulty if difficulty in PHRASES else "normal"
        script = selected_charset()
        phrase = rng.choice(TRADITIONAL_PHRASES[tier] if script == "traditional" else PHRASES[tier])
        chars = list(phrase)
        font_path = _font_path(script)
        positions = _sample_positions(rng, (width, height), len(chars))
        color = rng.choice([(32, 52, 120), (118, 42, 52), (36, 92, 66), (96, 58, 24), (74, 44, 110)])

        background, asset_id = make_background_asset((width, height), rng, soft=True)
        background = background.convert("RGBA")
        for char, (x, y) in zip(chars, positions, strict=True):
            font = ImageFont.truetype(font_path, rng.randint(28, 40))
            glyph = _render_glyph(char, font, color)
            glyph = glyph.rotate(rng.randint(-25, 25), resample=Image.Resampling.BICUBIC, expand=True)
            background.alpha_composite(glyph, (x - glyph.width // 2, y - glyph.height // 2))
        background = background.convert("RGB")

        tip = _render_tip(chars, color, font_path)
        return CaptchaChallenge(
            type=self.type,
            secret={
                "points": [{"x": x, "y": y} for x, y in positions],
                "order": phrase,
                "asset_id": asset_id,
            },
            payload={
                "background": png_data_uri(background),
                "tip": png_data_uri(tip),
                "width": width,
                "height": height,
                "count": len(chars),
            },
        )

    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        return super().verify(secret, answer, difficulty)
