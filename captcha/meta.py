#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码能力投影与只读辅助（从 services 拆出，避免编排文件超行数门禁）。"""

from typing import Any

from django.conf import settings

from captcha import cache as captcha_cache
from captcha.backends import all_backends, get_backend
from captcha.backends.art import png_data_uri
from captcha.const import (
    CAPTCHA_MODE_INTERACTIVE,
    captcha_globally_enabled,
    captcha_scene_enabled,
    current_captcha_mode,
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
    """当前启用的交互式题型；总开关关闭、图片模式或场景关闭时返回空列表。"""
    if not captcha_globally_enabled() or current_captcha_mode() != CAPTCHA_MODE_INTERACTIVE:
        return []
    if scene is not None and not captcha_scene_enabled(scene):
        return []
    configured = getattr(settings, "CAPTCHA_INTERACTIVE_TYPES", None)
    if isinstance(configured, str):
        configured = [item.strip() for item in configured.split(",") if item.strip()]
    if not isinstance(configured, (list, tuple)) or not configured:
        configured = list(all_backends())
    available = all_backends()
    return [captcha_type for captcha_type in configured if captcha_type in available]


def build_captcha_meta(scene: str | None = None) -> dict[str, Any]:
    """能力清单投影：模式 + 当前可用题型 + 图片码回退 + 中性 flow 标记。"""
    mode = current_captcha_mode()
    interactive = mode == CAPTCHA_MODE_INTERACTIVE
    types = enabled_captcha_types(scene) if interactive else []
    scene_enabled = captcha_globally_enabled() and (scene is None or captcha_scene_enabled(scene))
    image_fallback = interactive and scene_enabled and bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", False))
    captcha_flow = "adaptive" if current_risk_mode() != "off" else "always"
    return {"mode": mode, "types": types, "image_fallback": image_fallback, "captcha_flow": captcha_flow}


def preview_image_captcha() -> dict[str, Any]:
    """按已保存的图片验证码配置生成一张预览图（不写 CaptchaStore）。"""
    from captcha.helpers import get_challenge
    from captcha.views import render_challenge

    challenge, response = get_challenge()()
    image = render_challenge(challenge, 1)
    return {"image": png_data_uri(image), "length": len(str(response))}


def preview_interactive_challenge(captcha_type: str) -> dict[str, Any]:
    """按已保存的交互式配置生成一题预览（不写挑战缓存、不签发 ticket）。"""
    from captcha.errors import CaptchaInvalidError

    backend = get_backend(captcha_type)
    if backend is None:
        raise CaptchaInvalidError()
    generated = backend.generate()
    return {"type": generated.type, **generated.payload}


def inspect_challenge(challenge_id: str) -> dict[str, Any] | None:
    """受控检查端点专用：返回挑战答案（仅 E2E 配置开启时路由可达）。"""
    try:
        stored = captcha_cache.load_challenge(challenge_id)
    except Exception:  # noqa: BLE001 检查端点失败按不存在处理
        return None
    if not stored:
        return None
    return {"type": stored.get("type"), "scene": stored.get("scene"), "answer": stored.get("secret")}
