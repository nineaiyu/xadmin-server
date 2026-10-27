#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""图片验证码渲染核心（无 DB / 无路由依赖）。

从旧图片码视图中抽出，供统一协议的 ``image`` 题型复用；旧 DB 存储与
``captcha_key`` 链路已删除。
"""

import random
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from PIL import Image, ImageDraw, ImageFont

from captcha.helpers import filter_functions, makeimg, noise_functions

# Distance of the drawn text from the top of the captcha image
DISTANCE_FROM_TOP = 4


def getsize(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> tuple[int, int]:
    """文本包围盒宽高（兼容新老 Pillow 接口）。"""
    if hasattr(font, "getbbox"):
        _top, _left, _right, _bottom = font.getbbox(text)
        return int(_right - _left), int(_bottom - _top)

    legacy: Any = font
    if hasattr(legacy, "getoffset"):
        size = legacy.getsize(text)
        offset = legacy.getoffset(text)
        pairs = [int(x + y) for x, y in zip(size, offset, strict=True)]
        return pairs[0], pairs[1]
    legacy_size = legacy.getsize(text)
    return int(legacy_size[0]), int(legacy_size[1])


def _resolve_font_path() -> str:
    font_path: str | list[str] = settings.CAPTCHA_FONT_PATH
    if isinstance(font_path, str):
        return font_path
    if isinstance(font_path, (list, tuple)):
        return random.choice(font_path)
    raise ImproperlyConfigured("settings.CAPTCHA_FONT_PATH needs to be a path to a font or list of paths to fonts")


def _load_font(fontpath: str, scale: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if fontpath.lower().strip().endswith("ttf"):
        font = ImageFont.truetype(fontpath, settings.CAPTCHA_FONT_SIZE * scale)
    else:
        font = ImageFont.load(fontpath)
    return font


def _split_challenge_chars(text: str) -> list[str]:
    """标点归并到前一个字符（与 django-simple-captcha 原实现同口径）。"""
    charlist: list[str] = []
    for char in text:
        if char in settings.CAPTCHA_PUNCTUATION and len(charlist) >= 1:
            charlist[-1] += char
        else:
            charlist.append(char)
    return charlist


def render_challenge(text: str, scale: int, rng: random.Random | None = None) -> Image.Image:
    """绘制挑战文本；答案随机性由调用方负责，本函数只做确定性绘制。"""
    rng = rng or random.Random()
    font = _load_font(_resolve_font_path(), scale)
    if settings.CAPTCHA_IMAGE_SIZE:
        size = settings.CAPTCHA_IMAGE_SIZE
    else:
        size = getsize(font, text)
        size = (size[0] * 2, int(size[1] * 1.4))

    image = makeimg(size, settings.CAPTCHA_BACKGROUND_COLOR)
    xpos = 2

    charlist = _split_challenge_chars(text)
    for char in charlist:
        fgimage = makeimg(size, settings.CAPTCHA_FOREGROUND_COLOR)
        charimage = Image.new("L", getsize(font, f" {char} "), "#000000")
        chardraw = ImageDraw.Draw(charimage)
        chardraw.text((0, 0), f" {char} ", font=font, fill="#ffffff")
        if settings.CAPTCHA_LETTER_ROTATION:
            charimage = charimage.rotate(
                rng.randrange(*settings.CAPTCHA_LETTER_ROTATION),
                expand=0,
                resample=Image.Resampling.BICUBIC,
            )
        charimage = charimage.crop(charimage.getbbox())
        maskimage = Image.new("L", size)

        maskimage.paste(
            charimage,
            (
                xpos,
                DISTANCE_FROM_TOP,
                xpos + charimage.size[0],
                DISTANCE_FROM_TOP + charimage.size[1],
            ),
        )
        size = maskimage.size
        image = Image.composite(fgimage, image, maskimage)
        xpos = xpos + 2 + charimage.size[0]

    if settings.CAPTCHA_IMAGE_SIZE:
        tmpimg = makeimg(size, settings.CAPTCHA_BACKGROUND_COLOR)
        tmpimg.paste(
            image,
            (
                int((size[0] - xpos) / 2),
                int((size[1] - charimage.size[1]) / 2 - DISTANCE_FROM_TOP),
            ),
        )
        image = tmpimg.crop((0, 0, size[0], size[1]))
    else:
        image = image.crop((0, 0, xpos + 1, size[1]))
    draw = ImageDraw.Draw(image)

    for noise_fn in noise_functions():
        draw = noise_fn(draw, image)
    for filter_fn in filter_functions():
        image = filter_fn(image)
    return image
