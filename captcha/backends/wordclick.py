#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文字点选题型：按提示条顺序依次点击图中 3 个汉字。

字符集默认取 GB2312 一级字库（3755 个常用汉字，按拼音序），可通过
``CAPTCHA_CLICK_CHARSET=ascii`` 切换为字母数字；字体缺失时抛
``ImproperlyConfigured``（服务端 fail-fast，不静默降级成英文点选）。
"""

import random
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, ImageDraw, ImageFont

from captcha.backends.art import make_background_asset, png_data_uri, size_setting
from captcha.backends.base import CaptchaChallenge, ChallengeBackend, difficulty_factor

CLICK_COUNT = 3
MIN_DISTANCE = 48
MARGIN = 42
ASCII_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
#: 易混淆字（形近/多一笔）：点选要求按顺序点击，混入这些字会显著抬高真人失败率
CONFUSABLE_CHARS = set("己已巳日曰木术未末土士干千天夫失矢王玉主住八入人个几儿了子孑")


def _click_size() -> Any:
    """点选画布尺寸：独立配置优先，未配置时沿用滑块尺寸（兼容既有部署）。"""
    return getattr(settings, "CAPTCHA_CLICK_SIZE", None) or getattr(settings, "CAPTCHA_SLIDE_SIZE", None)


def _gb2312_level1_chars() -> str:
    chars: list[str] = []
    for high in range(0xB0, 0xD8):
        for low in range(0xA1, 0xFF):
            try:
                char = bytes((high, low)).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= char <= "\u9fff":
                chars.append(char)
    return "".join(dict.fromkeys(chars))


def _big5_level1_chars() -> str:
    chars: list[str] = []
    for lead in range(0xA4, 0xC7):
        for trail in [*range(0x40, 0x7F), *range(0xA1, 0xFF)]:
            try:
                char = bytes((lead, trail)).decode("big5")
            except UnicodeDecodeError:
                continue
            if "\u4e00" <= char <= "\u9fff":
                chars.append(char)
    return "".join(dict.fromkeys(chars))


COMMON_CHARS = _gb2312_level1_chars()
TRADITIONAL_CHARS = _big5_level1_chars()


def selected_charset() -> str:
    """当前题目的脚本：mixed 按整题随机，不在同一题内混排简繁。"""
    configured = str(getattr(settings, "CAPTCHA_CLICK_CHARSET", "simplified") or "simplified").strip().lower()
    if configured == "ascii":
        return "ascii"
    if configured == "traditional":
        return "traditional"
    if configured == "mixed":
        return random.choice(("simplified", "traditional"))
    return "simplified"


def click_charset(script: str | None = None) -> str:
    script = script or selected_charset()
    if script == "ascii":
        return ASCII_CHARS
    if script == "traditional":
        return "".join(char for char in TRADITIONAL_CHARS if char not in CONFUSABLE_CHARS)
    return "".join(char for char in COMMON_CHARS if char not in CONFUSABLE_CHARS)


def _font_path(script: str = "simplified") -> str:
    key = "CAPTCHA_CLICK_TRADITIONAL_FONT_PATH" if script == "traditional" else "CAPTCHA_CLICK_FONT_PATH"
    path = Path(str(getattr(settings, key, "")))
    if not path.is_file():
        raise ImproperlyConfigured(f"Captcha click font not found: {path}")
    return str(path)


def _sample_positions(rng: random.Random, size: tuple[int, int], count: int) -> list[tuple[int, int]]:
    width, height = size
    positions: list[tuple[int, int]] = []
    for index in range(count):
        for _ in range(400):
            point = (rng.randint(MARGIN, width - MARGIN), rng.randint(MARGIN, height - MARGIN))
            if all((point[0] - x) ** 2 + (point[1] - y) ** 2 >= MIN_DISTANCE**2 for x, y in positions):
                positions.append(point)
                break
        else:
            # 极端窄图兜底：等距网格，保证仍能出题而不是无限重试
            step = (width - 2 * MARGIN) // max(count - 1, 1)
            positions.append((MARGIN + index * step, height // 2))
    return positions


def _render_glyph(char: str, font: ImageFont.FreeTypeFont, color: tuple[int, int, int]) -> Image.Image:
    bbox = font.getbbox(char)
    padding = 8
    width = int(max(bbox[2] - bbox[0], 1)) + padding * 2
    height = int(max(bbox[3] - bbox[1], 1)) + padding * 2
    glyph = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)
    draw.text(
        (padding - bbox[0] + 1, padding - bbox[1] + 1),
        char,
        font=font,
        fill=(30, 34, 46, 70),
    )
    draw.text((padding - bbox[0], padding - bbox[1]), char, font=font, fill=(*color, 255))
    return glyph


def _render_tip(chars: list[str], color: tuple[int, int, int], font_path: str) -> Image.Image:
    slot = 46
    tip = Image.new("RGB", (slot * len(chars) + 24, 56), (250, 250, 252))
    draw = ImageDraw.Draw(tip)
    draw.rectangle((0, 0, tip.width - 1, tip.height - 1), outline=(206, 212, 226), width=1)
    font = ImageFont.truetype(font_path, 30)
    for index, char in enumerate(chars):
        bbox = font.getbbox(char)
        text_width = bbox[2] - bbox[0]
        x = 12 + index * slot + (slot - text_width) // 2
        draw.text((x, (tip.height - (bbox[3] - bbox[1])) // 2 - bbox[1]), char, font=font, fill=color)
    return tip


class WordClickBackend(ChallengeBackend):
    type = "click"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        rng = random.Random()
        width, height = size_setting(_click_size(), (320, 180))
        script = selected_charset()
        charset = click_charset(script)
        count = CLICK_COUNT
        if difficulty == "hard":
            count = max(int(getattr(settings, "CAPTCHA_CLICK_COUNT_HARD", 5) or 5), CLICK_COUNT)
        elif difficulty == "easy":
            count = max(int(getattr(settings, "CAPTCHA_CLICK_COUNT_EASY", 3) or 3), 2)
        if len(charset) < count:
            raise ImproperlyConfigured("CAPTCHA_CLICK_CHARSET needs at least 3 characters")
        chars = rng.sample(charset, count)
        font_path = _font_path(script)
        positions = _sample_positions(rng, (width, height), count)
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
            secret={"points": [{"x": x, "y": y} for x, y in positions], "asset_id": asset_id},
            payload={
                "background": png_data_uri(background),
                "tip": png_data_uri(tip),
                "width": width,
                "height": height,
                "count": count,
            },
        )

    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        if not isinstance(answer, dict):
            return False
        points = answer.get("points")
        expected = secret.get("points")
        if not isinstance(points, list) or not isinstance(expected, list) or len(points) != len(expected):
            return False
        radius = float(getattr(settings, "CAPTCHA_CLICK_RADIUS", 20) or 20) * difficulty_factor(difficulty, "click")
        for point, target in zip(points, expected, strict=True):
            if not isinstance(point, dict):
                return False
            x, y = point.get("x"), point.get("y")
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
            ):
                return False
            if (float(x) - float(target["x"])) ** 2 + (float(y) - float(target["y"])) ** 2 > radius**2:
                return False
        return True
