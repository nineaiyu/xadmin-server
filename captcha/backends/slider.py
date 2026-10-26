#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""滑动拼图题型：底图随机开缺口 + 同尺寸透明画布拼块。

拼块绘制在其真实 y 坐标上，前端只需水平拖动，答案只有 ``x``；
容差取 ``CAPTCHA_SLIDE_TOLERANCE``（默认 4px）。
"""

import random
from typing import Any

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter

from captcha.backends.art import make_background, png_data_uri, size_setting
from captcha.backends.base import CaptchaChallenge, ChallengeBackend

PIECE_SIZE = 56
MASK_SHAPES = 3


def _piece_mask(shape: int) -> Image.Image:
    """拼图形状遮罩：圆角主体 + 上/下/侧凸钮（三种轮换防形状指纹）。"""
    mask = Image.new("L", (PIECE_SIZE, PIECE_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    body_top = 14 if shape != 2 else 6
    body_bottom = PIECE_SIZE - 6 if shape != 1 else PIECE_SIZE - 14
    draw.rounded_rectangle((5, body_top, PIECE_SIZE - 5, body_bottom), radius=10, fill=255)
    nub = 10
    center = PIECE_SIZE // 2
    if shape == 0:
        draw.ellipse((center - nub, 0, center + nub, body_top + nub), fill=255)
    elif shape == 1:
        draw.ellipse((center - nub, body_bottom - nub, center + nub, PIECE_SIZE), fill=255)
    else:
        draw.ellipse((0, center - nub, body_top + nub, center + nub), fill=255)
    return mask


def _paint_hole(background: Image.Image, mask: Image.Image, x: int, y: int) -> Image.Image:
    """在底图上画出拼图形状的内阴影缺口。"""
    width, height = background.size
    full_mask = Image.new("L", (width, height), 0)
    full_mask.paste(mask, (x, y))
    shadow_mask = full_mask.point(lambda value: int(value * 0.5))
    dark = Image.new("RGB", (width, height), (24, 27, 42))
    background = Image.composite(dark, background, shadow_mask)
    edge = full_mask.filter(ImageFilter.FIND_EDGES)
    edge = edge.point(lambda value: min(255, value * 3))
    highlight = Image.new("RGB", (width, height), (244, 246, 255))
    return Image.composite(highlight, background, edge)


def _cut_piece(background: Image.Image, mask: Image.Image, x: int, y: int) -> Image.Image:
    """从底图裁出拼块并叠白色描边，放在整图大小的透明画布上。"""
    width, height = background.size
    piece = Image.new("RGBA", (PIECE_SIZE, PIECE_SIZE), (0, 0, 0, 0))
    crop = background.crop((x, y, x + PIECE_SIZE, y + PIECE_SIZE)).convert("RGBA")
    piece.paste(crop, (0, 0), mask)
    edge = mask.filter(ImageFilter.FIND_EDGES).point(lambda value: min(255, value * 2))
    stroke = Image.new("RGBA", (PIECE_SIZE, PIECE_SIZE), (255, 255, 255, 0))
    stroke.putalpha(edge.point(lambda value: int(value * 0.7)))
    piece = Image.alpha_composite(piece, stroke)

    # 拼块在画布上的 x 归零、仅保留真实 y：前端整幅透明画布按底图宽度叠加，
    # 再把 left 设为用户拖动到的 x 即可；若按秘密 x 绘制，前端无秘密值无法对齐。
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(piece, (0, y), piece)
    return canvas


class SlideBackend(ChallengeBackend):
    type = "slide"

    def generate(self) -> CaptchaChallenge:
        rng = random.Random()
        width, height = size_setting(getattr(settings, "CAPTCHA_SLIDE_SIZE", None), (320, 180))
        background = make_background((width, height), rng)
        margin = 6
        # 窄图（设置页允许 120px 起）下 0.25w 可能大于可放置上限：先夹紧再采样
        upper_x = max(width - PIECE_SIZE - margin, 0)
        lower_x = min(max(int(width * 0.25), 0), upper_x)
        x = rng.randint(lower_x, upper_x)
        upper_y = max(height - PIECE_SIZE - margin, 0)
        lower_y = min(int(height * 0.15), upper_y)
        y = rng.randint(lower_y, upper_y)
        mask = _piece_mask(rng.randrange(MASK_SHAPES))
        piece_canvas = _cut_piece(background, mask, x, y)
        background = _paint_hole(background, mask, x, y)
        return CaptchaChallenge(
            type=self.type,
            secret={"x": x},
            payload={
                "background": png_data_uri(background),
                "piece": png_data_uri(piece_canvas),
                "width": width,
                "height": height,
                "piece_size": PIECE_SIZE,
            },
        )

    def verify(self, secret: dict[str, Any], answer: Any) -> bool:
        if not isinstance(answer, dict):
            return False
        value = answer.get("x")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        tolerance = int(getattr(settings, "CAPTCHA_SLIDE_TOLERANCE", 4) or 4)
        return abs(float(value) - float(secret.get("x", -9999))) <= tolerance
