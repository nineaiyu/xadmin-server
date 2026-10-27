#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""图标点选题型：按提示顺序点击图中图标。

图标由 Pillow 程序化绘制（圆/三角/方/菱形/星/十字/六边形），不依赖第三方图标字体；
提示条与底图使用同一套绘制函数，干扰项与目标同风格，复用文字点选的命中校验。
"""

import math
import random

from django.conf import settings
from PIL import Image, ImageDraw

from captcha.backends.art import make_background_asset, png_data_uri, size_setting
from captcha.backends.base import CaptchaChallenge
from captcha.backends.wordclick import WordClickBackend, _sample_positions

SHAPES = ("circle", "triangle", "square", "diamond", "star", "cross", "hexagon")
ICON_SIZE = 46
COLORS = ((32, 52, 120), (118, 42, 52), (36, 92, 66), (96, 58, 24), (74, 44, 110))


def _polygon_star(center: tuple[int, int], radius: int, inner: int) -> list[tuple[int, int]]:
    points = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        r = radius if index % 2 == 0 else inner
        points.append((center[0] + int(math.cos(angle) * r), center[1] + int(math.sin(angle) * r)))
    return points


def _render_icon(shape: str, size: int, color: tuple[int, int, int], rng: random.Random) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = 5
    left, top, right, bottom = pad, pad, size - pad, size - pad
    if shape == "circle":
        draw.ellipse((left, top, right, bottom), fill=(*color, 255))
    elif shape == "triangle":
        draw.polygon([(size // 2, top), (right, bottom), (left, bottom)], fill=(*color, 255))
    elif shape == "square":
        draw.rounded_rectangle((left, top, right, bottom), radius=5, fill=(*color, 255))
    elif shape == "diamond":
        center = size // 2
        draw.polygon([(center, top), (right, center), (center, bottom), (left, center)], fill=(*color, 255))
    elif shape == "star":
        draw.polygon(_polygon_star((size // 2, size // 2), size // 2 - pad, size // 5), fill=(*color, 255))
    elif shape == "cross":
        arm = size // 3
        draw.rectangle((left, size // 2 - arm // 2, right, size // 2 + arm // 2), fill=(*color, 255))
        draw.rectangle((size // 2 - arm // 2, top, size // 2 + arm // 2, bottom), fill=(*color, 255))
    else:
        center = size // 2
        radius = size // 2 - pad
        points = [
            (
                center + int(math.cos(math.pi / 3 * index) * radius),
                center + int(math.sin(math.pi / 3 * index) * radius),
            )
            for index in range(6)
        ]
        draw.polygon(points, fill=(*color, 255))
    return image.rotate(rng.randint(-18, 18), resample=Image.Resampling.BICUBIC, expand=True)


def _render_tip(icons: list[Image.Image]) -> Image.Image:
    slot = ICON_SIZE + 16
    tip = Image.new("RGB", (slot * len(icons) + 24, slot + 12), (250, 250, 252))
    draw = ImageDraw.Draw(tip)
    draw.rectangle((0, 0, tip.width - 1, tip.height - 1), outline=(206, 212, 226), width=1)
    for index, icon in enumerate(icons):
        tip.paste(icon, (12 + index * slot, 12), icon)
    return tip


class IconBackend(WordClickBackend):
    type = "icon"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        rng = random.Random()
        width, height = size_setting(getattr(settings, "CAPTCHA_SLIDE_SIZE", None), (320, 180))
        count = 3 if difficulty != "hard" else 4
        if len(SHAPES) < count + 1:
            count = max(len(SHAPES) - 1, 2)
        targets = rng.sample(SHAPES, count)
        decoys = rng.sample([shape for shape in SHAPES if shape not in targets], min(count, len(SHAPES) - count))
        color = rng.choice(COLORS)
        positions = _sample_positions(rng, (width, height), count + len(decoys))
        background, asset_id = make_background_asset((width, height), rng, soft=True)
        background = background.convert("RGBA")
        icons: list[Image.Image] = []
        for shape, (x, y) in zip(targets + decoys, positions, strict=True):
            icon = _render_icon(shape, ICON_SIZE, color, rng)
            icons.append(icon)
            background.alpha_composite(icon, (x - icon.width // 2, y - icon.height // 2))
        background = background.convert("RGB")
        tip = _render_tip(icons[:count])
        return CaptchaChallenge(
            type=self.type,
            secret={"points": [{"x": x, "y": y} for x, y in positions[:count]], "asset_id": asset_id},
            payload={
                "background": png_data_uri(background),
                "tip": png_data_uri(tip),
                "width": width,
                "height": height,
                "count": count,
            },
        )
