#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码的程序化底图与绘制辅助。

不依赖外部图片资源：12 组浅色渐变配色 + 程序化噪点/几何纹理即可满足
「每次不同、无模板匹配指纹」的出题需求；后续若要换成 CC0 照片包，只需把
``make_background`` 的实现替换为读图，调用方无需改动。
"""

import base64
import random
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

#: 浅色渐变底（RGB 两端色），保证深色文字/拼块有足够对比度
BACKGROUND_PALETTES: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
    ((238, 244, 255), (196, 215, 245)),
    ((255, 243, 236), (244, 205, 186)),
    ((240, 250, 242), (190, 226, 201)),
    ((250, 244, 255), (214, 200, 242)),
    ((255, 250, 232), (240, 220, 168)),
    ((236, 250, 252), (186, 224, 232)),
    ((252, 238, 244), (232, 194, 214)),
    ((245, 246, 250), (204, 210, 228)),
    ((243, 249, 234), (205, 225, 180)),
    ((255, 247, 240), (238, 211, 190)),
    ((234, 246, 255), (188, 218, 240)),
    ((248, 245, 235), (218, 205, 180)),
)


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(start[index] + (end[index] - start[index]) * ratio) for index in range(3))  # type: ignore[return-value]


def _gradient(size: tuple[int, int], start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)], fill=_mix(start, end, y / max(height - 1, 1)))
    return image


def _decorate(image: Image.Image, rng: random.Random) -> Image.Image:
    """几何纹理：半透明圆/斜线，打散纯渐变的模板匹配特征。"""
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(3, 6)):
        radius = rng.randint(18, max(20, width // 5))
        x = rng.randint(-radius, width)
        y = rng.randint(-radius, height)
        color = (255, 255, 255, rng.randint(18, 42)) if rng.random() < 0.6 else (60, 70, 110, rng.randint(10, 24))
        draw.ellipse((x, y, x + radius, y + radius), fill=color)
    for _ in range(rng.randint(3, 5)):
        x1, y1 = rng.randint(0, width), rng.randint(0, height)
        x2 = rng.randint(0, width)
        draw.line((x1, y1, x2, height), fill=(255, 255, 255, rng.randint(16, 32)), width=rng.randint(1, 3))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    noise = Image.effect_noise(image.size, rng.uniform(10, 18)).convert("RGB")
    return Image.blend(image, noise, 0.035)


def make_background_asset(
    size: tuple[int, int],
    rng: random.Random | None = None,
    soft: bool = False,
) -> tuple[Image.Image, str]:
    """生成底图并返回资产 id；soft=True 时叠加浅色蒙层保证文字对比度。"""
    rng = rng or random.Random()
    from captcha.assets import select_background

    photo, asset_id = select_background(size, rng)
    if photo is not None:
        image = _decorate(photo, rng)
    else:
        start, end = rng.choice(BACKGROUND_PALETTES)
        image = _decorate(_gradient(size, start, end), rng)
        asset_id = "procedural"
    if soft:
        image = Image.blend(image, Image.new("RGB", size, (250, 250, 252)), 0.42)
    return image, asset_id


def make_background(
    size: tuple[int, int],
    rng: random.Random | None = None,
    soft: bool = False,
) -> Image.Image:
    image, _asset_id = make_background_asset(size, rng, soft)
    return image


def make_orientation_scene_asset(size: tuple[int, int], rng: random.Random | None = None) -> tuple[Image.Image, str]:
    """旋转题场景图 + 资产 id（图库图或程序化场景）。"""
    rng = rng or random.Random()
    from captcha.assets import select_background

    photo, asset_id = select_background(size, rng, orientation="landscape")
    if photo is not None:
        return _decorate(photo, rng), asset_id
    return make_orientation_scene(size, rng), "procedural-scene"


def make_orientation_scene(size: tuple[int, int], rng: random.Random | None = None) -> Image.Image:
    """旋转题专用场景图：天空 + 太阳 + 地平线 + 山。

    纯渐变图没有「正向」语义，用户无从判断是否转正；地平线、太阳位置与山体
    提供明确的上下方向，同时保留渐变色以抵抗模板匹配。不叠加箭头等人工标记，
    避免被误认为图片瑕疵。
    """
    rng = rng or random.Random()
    width, height = size
    start, end = rng.choice(BACKGROUND_PALETTES)
    image = _gradient(size, start, end)
    draw = ImageDraw.Draw(image)

    horizon = int(height * rng.uniform(0.58, 0.68))
    ground = _mix(end, (92, 120, 92), 0.42)
    draw.rectangle((0, horizon, width, height), fill=ground)

    sun_radius = rng.randint(14, 20)
    sun_x = rng.randint(int(width * 0.6), int(width * 0.82))
    sun_y = rng.randint(int(height * 0.18), int(height * 0.3))
    draw.ellipse(
        (sun_x - sun_radius, sun_y - sun_radius, sun_x + sun_radius, sun_y + sun_radius),
        fill=(255, 214, 126),
    )

    peak_x = rng.randint(int(width * 0.18), int(width * 0.38))
    peak_y = rng.randint(int(height * 0.3), int(height * 0.42))
    draw.polygon(
        [(peak_x - width // 4, horizon + 2), (peak_x, peak_y), (peak_x + width // 4, horizon + 2)],
        fill=_mix(ground, (48, 74, 58), 0.5),
    )
    draw.polygon(
        [
            (peak_x - width // 10, peak_y + height // 6),
            (peak_x, peak_y + 6),
            (peak_x + width // 10, peak_y + height // 6),
        ],
        fill=(246, 248, 252),
    )

    image = _decorate(image, rng)
    return image


def png_data_uri(image: Image.Image) -> str:
    """Pillow 图像 -> ``data:image/png;base64,``（前端可直接作为 img src）。"""
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def size_setting(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    """(width, height) 配置归一：容忍 list / tuple / 字符串，非法时回退默认。"""
    try:
        width, height = value
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        return default
    if width < 80 or height < 80:
        return default
    return width, height


__all__ = [
    "BACKGROUND_PALETTES",
    "make_background",
    "make_orientation_scene",
    "png_data_uri",
    "size_setting",
]
