#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""旋转回正题型：随机预转场景图，用户把图转回正向。

预转角避开 0/90/180/270 附近 ±15° 的平坦区（那些角度肉眼难判定）。
Pillow 的 rotate 正角为逆时针，前端 CSS rotate 正角为顺时针；用户把图转正时
施加的是顺时针 theta，因此答案取预转角本身（归一到 -180~180），而不是相反数。
"""

import math
import random
from typing import Any

from django.conf import settings
from PIL import Image, ImageDraw

from captcha.backends.art import make_orientation_scene, png_data_uri, size_setting
from captcha.backends.base import CaptchaChallenge, ChallengeBackend, difficulty_factor

ANGLE_RANGES = ((15, 75), (105, 165), (195, 255), (285, 345))


def normalize_angle(angle: float) -> float:
    """归一到 (-180, 180]。"""
    value = (float(angle) + 180.0) % 360.0 - 180.0
    return 180.0 if value == -180.0 else value


class RotateBackend(ChallengeBackend):
    type = "rotate"

    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        rng = random.Random()
        size = size_setting(getattr(settings, "CAPTCHA_ROTATE_SIZE", None), (240, 240))
        width, height = size
        # 先生成 sqrt(2) 倍大的场景再中心裁切：任意角度旋转后，目标方形完全落在
        # 原图内容内，不会露出 Pillow 扩边填充的黑色三角。
        source_size = (math.ceil(width * math.sqrt(2)) + 4, math.ceil(height * math.sqrt(2)) + 4)
        scene = make_orientation_scene(source_size, rng)
        low, high = rng.choice(ANGLE_RANGES)
        theta = rng.randint(low, high)
        rotated = scene.rotate(
            theta,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=(255, 255, 255),
        )
        left = max((rotated.width - width) // 2, 0)
        top = max((rotated.height - height) // 2, 0)
        rotated = rotated.crop((left, top, left + width, top + height))
        draw = ImageDraw.Draw(rotated)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(255, 255, 255), width=2)
        return CaptchaChallenge(
            type=self.type,
            secret={"angle": normalize_angle(theta), "pre_rotation": theta},
            payload={
                "background": png_data_uri(rotated),
                "width": width,
                "height": height,
            },
        )

    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        if not isinstance(answer, dict):
            return False
        value = answer.get("angle")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        tolerance = float(getattr(settings, "CAPTCHA_ROTATE_TOLERANCE", 4) or 4) * difficulty_factor(
            difficulty, "rotate"
        )
        expected = float(secret.get("angle", 0.0))
        difference = abs(normalize_angle(float(value) - expected))
        return difference <= tolerance
