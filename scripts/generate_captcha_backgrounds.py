#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""生成内置验证码背景图库（自绘，无第三方许可）。

产物：``captcha/resources/backgrounds/photo/*.webp`` + ``manifest.json``。

设计目标：每张图都有多图层结构（天空/远山/水面/城市/植物/分形纹理），避免
“单层渐变 + 两个圆”这种低熵底图；同时保持中低对比度，避免压过点选文字/拼块。
图片只含抽象几何与自然场景，不含人脸与可识别个体。
"""

import hashlib
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = ROOT / "captcha" / "resources" / "backgrounds"
PHOTO_DIR = PACK_DIR / "photo"
WIDTH, HEIGHT = 640, 360
SEED = 20261027

PALETTES = (
    ((232, 242, 255), (156, 186, 226), (44, 68, 112)),
    ((255, 240, 228), (238, 184, 158), (108, 62, 70)),
    ((236, 250, 240), (166, 214, 184), (42, 86, 70)),
    ((247, 240, 255), (196, 176, 232), (72, 54, 112)),
    ((255, 250, 226), (236, 210, 144), (116, 88, 38)),
    ((232, 250, 252), (150, 210, 220), (36, 88, 104)),
    ((255, 238, 246), (230, 168, 196), (104, 48, 78)),
    ((242, 244, 250), (186, 196, 220), (58, 70, 96)),
)


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def _mix(start, end, ratio):
    ratio = max(0.0, min(1.0, ratio))
    return tuple(_clamp(start[i] + (end[i] - start[i]) * ratio) for i in range(3))


def _gradient(size, top, bottom):
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        draw.line([(0, y), (size[0], y)], fill=_mix(top, bottom, y / max(size[1] - 1, 1)))
    return image


def _noise_layer(size, rng, cell: int) -> Image.Image:
    small = (max(2, size[0] // cell), max(2, size[1] // cell))
    data = bytes(rng.randrange(256) for _ in range(small[0] * small[1]))
    return Image.frombytes("L", small, data).resize(size, Image.Resampling.BICUBIC)


def _fractal_noise(size, rng, octaves: int = 5) -> Image.Image:
    base = Image.new("L", size, 128)
    for index in range(octaves):
        layer = _noise_layer(size, rng, max(4, 2 ** (octaves - index + 2)))
        base = Image.blend(base, layer, 0.45)
    return ImageOps.autocontrast(base.filter(ImageFilter.GaussianBlur(1.1)))


def _colorize(noise, palette):
    light, mid, dark = palette
    return ImageOps.colorize(noise, dark, mid, light)


def _grain(image, rng, opacity=0.06):
    noise = _noise_layer(image.size, rng, 2).convert("RGB")
    return Image.blend(image, noise, opacity)


def _soft_overlay(image, overlay):
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _vignette(image, strength=0.35):
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (-image.width // 3, -image.height // 3, image.width + image.width // 3, image.height + image.height // 3),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(90))
    dark = Image.new("RGB", image.size, (18, 22, 34))
    return Image.composite(image, Image.blend(image, dark, strength), mask)


def _bokeh(overlay, rng, count=18):
    draw = ImageDraw.Draw(overlay)
    for _ in range(count):
        radius = rng.randint(8, 42)
        x = rng.randint(-radius, WIDTH)
        y = rng.randint(-radius, HEIGHT)
        alpha = rng.randint(12, 42)
        draw.ellipse((x, y, x + radius, y + radius), fill=(255, 255, 255, alpha))


def _marble(rng, palette):
    noise = _fractal_noise((WIDTH, HEIGHT), rng, 6)
    warped = ImageChops.overlay(noise, _fractal_noise((WIDTH, HEIGHT), rng, 4))
    image = _colorize(warped, palette)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(4, 8)):
        x1, y1 = rng.randint(-100, WIDTH), rng.randint(-100, HEIGHT)
        x2, y2 = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        draw.line((x1, y1, x2, y2), fill=(255, 255, 255, rng.randint(18, 50)), width=rng.randint(2, 9))
    image = _soft_overlay(image.filter(ImageFilter.GaussianBlur(0.5)), overlay)
    return _vignette(_grain(image, rng), 0.18)


def _low_poly(rng, palette):
    image = _gradient((WIDTH, HEIGHT), palette[0], palette[1])
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cols, rows = 10, 6
    points = [
        [
            (
                int(x * WIDTH / cols + rng.randint(-28, 28)),
                int(y * HEIGHT / rows + rng.randint(-24, 24)),
            )
            for x in range(cols + 1)
        ]
        for y in range(rows + 1)
    ]
    for y in range(rows):
        for x in range(cols):
            p00, p10 = points[y][x], points[y][x + 1]
            p01, p11 = points[y + 1][x], points[y + 1][x + 1]
            for triangle in ((p00, p10, p01), (p10, p11, p01)):
                ratio = rng.random()
                color = _mix(palette[1], palette[2], ratio * 0.65)
                draw.polygon(triangle, fill=(*color, rng.randint(90, 150)))
    image = _soft_overlay(image, overlay).filter(ImageFilter.GaussianBlur(0.6))
    return _vignette(_grain(image, rng, 0.04), 0.2)


def _fabric(rng, palette):
    noise = _fractal_noise((WIDTH, HEIGHT), rng, 5)
    image = _colorize(noise, palette)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index in range(18):
        x = int(index * WIDTH / 18 + rng.randint(-10, 10))
        draw.line(
            (x, 0, x + rng.randint(-90, 90), HEIGHT), fill=(255, 255, 255, rng.randint(10, 30)), width=rng.randint(1, 4)
        )
    for index in range(10):
        y = int(index * HEIGHT / 10 + rng.randint(-8, 8))
        draw.line(
            (0, y, WIDTH, y + rng.randint(-30, 30)), fill=(20, 24, 36, rng.randint(8, 18)), width=rng.randint(1, 3)
        )
    image = _soft_overlay(image, overlay).filter(ImageFilter.GaussianBlur(0.8))
    return _vignette(_grain(image, rng, 0.08), 0.22)


def _botanical(rng, palette):
    image = _gradient((WIDTH, HEIGHT), palette[0], palette[1])
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(5, 9)):
        x = rng.randint(-40, WIDTH + 40)
        y = rng.randint(HEIGHT // 3, HEIGHT + 80)
        length = rng.randint(90, 220)
        width = rng.randint(22, 48)
        angle = rng.uniform(-0.9, 0.9)
        tip = (x + int(math.sin(angle) * length), y - int(math.cos(angle) * length))
        left = (x - width, y)
        right = (x + width, y)
        color = _mix(palette[2], palette[1], rng.random() * 0.7)
        draw.polygon([left, tip, right], fill=(*color, rng.randint(60, 115)))
        draw.line((x, y, tip[0], tip[1]), fill=(*palette[0], rng.randint(40, 80)), width=1)
    _bokeh(overlay, rng, 12)
    image = _soft_overlay(image.filter(ImageFilter.GaussianBlur(1.2)), overlay)
    return _vignette(_grain(image, rng, 0.05), 0.24)


def _mountain_layer(draw, rng, y_base, amplitude, color, alpha, peaks):
    points = [(0, HEIGHT)]
    step = WIDTH // peaks
    for index in range(peaks + 1):
        x = index * step
        y = y_base + rng.randint(-amplitude, amplitude)
        points.append((x, y))
        if index < peaks:
            points.append((x + step // 2, y_base + rng.randint(-amplitude, amplitude)))
    points.append((WIDTH, HEIGHT))
    draw.polygon(points, fill=(*color, alpha))


def _landscape(rng, palette, water=False):
    image = _gradient((WIDTH, HEIGHT), palette[0], palette[1])
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    horizon = int(HEIGHT * rng.uniform(0.52, 0.62))
    sun_radius = rng.randint(26, 48)
    sun_x = rng.randint(int(WIDTH * 0.55), int(WIDTH * 0.82))
    sun_y = rng.randint(int(HEIGHT * 0.15), int(HEIGHT * 0.3))
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (sun_x - sun_radius * 3, sun_y - sun_radius * 3, sun_x + sun_radius * 3, sun_y + sun_radius * 3),
        fill=(255, 222, 150, 45),
    )
    overlay = Image.alpha_composite(overlay, glow.filter(ImageFilter.GaussianBlur(28)))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(
        (sun_x - sun_radius, sun_y - sun_radius, sun_x + sun_radius, sun_y + sun_radius),
        fill=(255, 226, 156, 235),
    )
    _mountain_layer(draw, rng, horizon - 34, 18, palette[1], 170, rng.randint(3, 5))
    _mountain_layer(draw, rng, horizon - 8, 30, palette[2], 150, rng.randint(2, 4))
    forest = _mix(palette[2], (28, 48, 36), 0.45)
    for _ in range(rng.randint(16, 28)):
        x = rng.randint(0, WIDTH)
        height = rng.randint(18, 52)
        draw.polygon(
            [(x - height // 3, horizon + 6), (x, horizon - height), (x + height // 3, horizon + 6)],
            fill=(*forest, rng.randint(100, 170)),
        )
    if water:
        water_top = horizon + 8
        draw.rectangle((0, water_top, WIDTH, HEIGHT), fill=(*_mix(palette[1], palette[2], 0.4), 150))
        for _ in range(rng.randint(20, 36)):
            y = rng.randint(water_top, HEIGHT)
            x1 = rng.randint(0, WIDTH)
            draw.line((x1, y, x1 + rng.randint(20, 140), y), fill=(255, 255, 255, rng.randint(16, 46)), width=1)
    else:
        draw.rectangle((0, horizon + 10, WIDTH, HEIGHT), fill=(*_mix(palette[1], palette[2], 0.55), 150))
    image = _soft_overlay(image, overlay.filter(ImageFilter.GaussianBlur(0.5)))
    return _vignette(_grain(image, rng, 0.045), 0.2)


def _city(rng, palette):
    image = _gradient((WIDTH, HEIGHT), palette[0], palette[1])
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    horizon = int(HEIGHT * rng.uniform(0.62, 0.7))
    sun_x, sun_y = rng.randint(80, WIDTH - 80), rng.randint(50, 120)
    draw.ellipse((sun_x - 34, sun_y - 34, sun_x + 34, sun_y + 34), fill=(255, 224, 170, 120))
    for layer in range(3):
        alpha = 90 + layer * 45
        color = _mix(palette[1], palette[2], 0.35 + layer * 0.2)
        x = -20
        while x < WIDTH:
            width = rng.randint(30, 75)
            height = rng.randint(40, 130) + layer * 10
            top = horizon - height
            draw.rectangle((x, top, x + width, horizon), fill=(*color, alpha))
            for wx in range(x + 8, x + width - 8, 14):
                for wy in range(top + 10, horizon - 8, 18):
                    if rng.random() < 0.35:
                        draw.rectangle((wx, wy, wx + 4, wy + 6), fill=(255, 232, 170, rng.randint(35, 90)))
            x += width + rng.randint(4, 14)
    draw.rectangle((0, horizon, WIDTH, HEIGHT), fill=(*_mix(palette[1], palette[2], 0.45), 160))
    for _ in range(28):
        y = rng.randint(horizon, HEIGHT)
        x1 = rng.randint(0, WIDTH)
        draw.line((x1, y, x1 + rng.randint(16, 100), y), fill=(255, 255, 255, rng.randint(14, 42)), width=1)
    image = _soft_overlay(image, overlay)
    return _vignette(_grain(image, rng, 0.05), 0.24)


def main() -> None:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    files = []
    generators = (
        ("abstract", _marble, False),
        ("abstract", _low_poly, False),
        ("abstract", _fabric, False),
        ("abstract", _botanical, False),
        ("scene", _landscape, True),
        ("scene", _city, True),
    )
    for index in range(24):
        prefix, generator, landscape = generators[index % len(generators)]
        palette = PALETTES[index % len(PALETTES)]
        if generator is _landscape:
            image = _landscape(rng, palette, water=index % 2 == 0)
        else:
            image = generator(rng, palette)
        path = PHOTO_DIR / f"{prefix}-{index + 1:02d}.webp"
        image.save(path, "WEBP", quality=86, method=6)
        files.append(
            {
                "id": path.stem,
                "file": f"photo/{path.name}",
                "orientation": "landscape" if landscape else "",
                "width": WIDTH,
                "height": HEIGHT,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "pack_id": "builtin",
        "version": "3",
        "license": "self",
        "source": "generated by scripts/generate_captcha_backgrounds.py",
        "description": "Self-drawn multi-layer scenes and fractal textures; no third-party images.",
        "files": files,
    }
    (PACK_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    keep = {Path(item["file"]).name for item in files}
    for stale in PHOTO_DIR.glob("*.webp"):
        if stale.name not in keep:
            stale.unlink()
    print(f"generated {len(files)} backgrounds in {PHOTO_DIR}")


if __name__ == "__main__":
    main()
