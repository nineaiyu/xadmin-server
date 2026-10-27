#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码能力投影与只读辅助（从 services 拆出，避免编排文件超行数门禁）。"""

from typing import Any

from django.conf import settings

from captcha.backends import all_backends, get_backend
from captcha.const import (
    CAPTCHA_PUZZLE_TYPES,
    CAPTCHA_TYPE_IMAGE,
    CAPTCHA_TYPE_POW,
    captcha_globally_enabled,
    captcha_scene_enabled,
    current_risk_mode,
)


def challenge_ttl() -> int:
    return max(int(getattr(settings, "CAPTCHA_CHALLENGE_TTL", 120) or 120), 10)


def ticket_ttl() -> int:
    return max(int(getattr(settings, "CAPTCHA_TICKET_TTL", 120) or 120), 10)


def max_attempts() -> int:
    return max(int(getattr(settings, "CAPTCHA_MAX_ATTEMPTS", 3) or 3), 1)


def track_required() -> bool:
    return bool(getattr(settings, "CAPTCHA_TRACK_CHECK_ENABLED", True))


def enabled_captcha_types(scene: str | None = None) -> list[str]:
    """当前启用的题型；图片题型仅在显式回退开关开启时加入（权重 0，不进随机池）。"""
    if not captcha_globally_enabled():
        return []
    if scene is not None and not captcha_scene_enabled(scene):
        return []
    configured = getattr(settings, "CAPTCHA_ENABLED_TYPES", None)
    if isinstance(configured, str):
        configured = [item.strip() for item in configured.split(",") if item.strip()]
    available = all_backends()
    if not isinstance(configured, (list, tuple)) or not configured:
        configured = list(CAPTCHA_PUZZLE_TYPES)
    types = [
        captcha_type for captcha_type in configured if captcha_type in available and captcha_type != CAPTCHA_TYPE_POW
    ]
    if not types:
        types = [captcha_type for captcha_type in CAPTCHA_PUZZLE_TYPES if captcha_type in available]
    if bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", True)) and CAPTCHA_TYPE_IMAGE in available:
        if CAPTCHA_TYPE_IMAGE not in types:
            types.append(CAPTCHA_TYPE_IMAGE)
    return types


def build_captcha_meta(scene: str | None = None) -> dict[str, Any]:
    """能力清单投影：当前可用题型 + 图片回退 + 中性 flow 标记。"""
    types = enabled_captcha_types(scene)
    scene_enabled = captcha_globally_enabled() and (scene is None or captcha_scene_enabled(scene))
    image_fallback = scene_enabled and CAPTCHA_TYPE_IMAGE in types
    captcha_flow = "adaptive" if current_risk_mode() != "off" else "always"
    return {"types": types, "image_fallback": image_fallback, "captcha_flow": captcha_flow}


def preview_image_captcha() -> dict[str, Any]:
    """按已保存的图片验证码配置生成一张预览图（不写挑战缓存）。"""
    from captcha.backends.image import ImageBackend

    generated = ImageBackend().generate()
    payload = generated.payload
    return {"image": payload.get("background", ""), "length": int(payload.get("length") or 0)}


def inspect_challenge(challenge_id: str) -> dict[str, Any] | None:
    """E2E 受控检查：返回挑战答案；仅 CAPTCHA_INSPECT_ENABLED + 专用 token 可达。"""
    from captcha import cache as captcha_cache

    try:
        stored = captcha_cache.load_challenge(challenge_id)
    except Exception:  # noqa: BLE001 检查端点失败按不存在处理
        return None
    if not stored:
        return None
    return {"type": stored.get("type"), "scene": stored.get("scene"), "answer": stored.get("secret")}


def preview_interactive_challenge(captcha_type: str) -> dict[str, Any]:
    """按已保存的交互式配置生成一题预览（不写挑战缓存、不签发 ticket）。"""
    from captcha.errors import CaptchaInvalidError

    backend = get_backend(captcha_type)
    if backend is None:
        raise CaptchaInvalidError()
    generated = backend.generate()
    return {"type": generated.type, **generated.payload}
