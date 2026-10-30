# -*- coding: utf-8 -*-
"""图片码渲染：墨迹包围盒与绘制偏移（防止字形被右侧裁掉）。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from captcha.rendering import getsize, render_challenge

FONT_PATH = str(Path(__file__).resolve().parents[3] / "captcha/fonts/Vera.ttf")


def _font(size: int = 26) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _true_ink_width(char: str) -> int:
    """在同一字体下用足够大的画布绘制，取真实墨迹宽度（不受包围盒口径影响）。"""
    probe = Image.new("L", (240, 240), 0)
    ImageDraw.Draw(probe).text((0, 0), f" {char} ", font=_font(), fill=255)
    box = probe.getbbox()
    assert box is not None
    return int(box[2] - box[0])


def test_getsize_uses_ink_axes():
    font = _font()
    left, top, right, bottom = font.getbbox(" WW ")
    # getbbox 返回 (left, top, right, bottom)：宽高必须按同轴相减
    assert getsize(font, " WW ") == (right - left, bottom - top)
    assert getsize(font, " WW ") != (right - top, bottom - left)


def test_render_keeps_full_glyph_width(settings):
    settings.CAPTCHA_FONT_PATH = FONT_PATH
    settings.CAPTCHA_IMAGE_SIZE = None
    settings.CAPTCHA_NOISE_FUNCTIONS = ()
    settings.CAPTCHA_FILTER_FUNCTIONS = ()
    settings.CAPTCHA_LETTER_ROTATION = None
    # "=" 的字形墨迹在旧实现里会被画布右边界切掉一截（top 偏大、宽度按错轴）
    image = render_challenge("=", 1)
    assert image.width >= _true_ink_width("=") + 3
