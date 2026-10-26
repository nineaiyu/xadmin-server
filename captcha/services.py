#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""captcha app 对外服务契约层。

其他 app 需要使用验证码能力时，只允许从本模块导入：

- ``CaptchaAuth``：既有图片验证码（DB 存储、一次性消费）；
- ``InteractiveCaptcha``：交互式验证码编排（挑战 / 校验 / ticket 消费）；
- ``build_captcha_meta`` / ``enabled_captcha_types``：能力清单投影。

禁止直接 import ``captcha.utils`` / ``captcha.backends`` 等内部实现，
避免 app 间横向依赖扩散。
"""

import random
import uuid
from typing import Any

from django.conf import settings

from captcha import cache as captcha_cache
from captcha.backends import all_backends, get_backend
from captcha.backends.art import png_data_uri
from captcha.backends.track import validate_track
from captcha.const import (
    CAPTCHA_MODE_INTERACTIVE,
    CAPTCHA_SCENE_SWITCHES,
    CAPTCHA_TYPE_CLICK,
    captcha_globally_enabled,
    captcha_scene_enabled,
    current_captcha_mode,
)
from captcha.utils import CaptchaAuth


class CaptchaServiceError(Exception):
    """交互式验证码业务失败基类；视图按 ``code`` 返回 HTTP 400。"""

    code = "captcha_invalid"


class CaptchaInvalidError(CaptchaServiceError):
    code = "captcha_invalid"


class CaptchaExhaustedError(CaptchaServiceError):
    code = "captcha_exhausted"


def challenge_ttl() -> int:
    return max(int(getattr(settings, "CAPTCHA_CHALLENGE_TTL", 120) or 120), 10)


def ticket_ttl() -> int:
    return max(int(getattr(settings, "CAPTCHA_TICKET_TTL", 120) or 120), 10)


def max_attempts() -> int:
    return max(int(getattr(settings, "CAPTCHA_MAX_ATTEMPTS", 3) or 3), 1)


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
    """能力清单投影：模式 + 当前可用题型 + 图片码回退开关。"""
    mode = current_captcha_mode()
    interactive = mode == CAPTCHA_MODE_INTERACTIVE
    types = enabled_captcha_types(scene) if interactive else []
    scene_enabled = captcha_globally_enabled() and (scene is None or captcha_scene_enabled(scene))
    image_fallback = interactive and scene_enabled and bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", False))
    return {"mode": mode, "types": types, "image_fallback": image_fallback}


def _track_required() -> bool:
    return bool(getattr(settings, "CAPTCHA_TRACK_CHECK_ENABLED", True))


class InteractiveCaptcha:
    """交互式验证码编排：生成挑战 -> 服务端校验 -> 签发一次性 ticket。"""

    def challenge(self, captcha_type: str | None = None, scene: str = "login") -> dict[str, Any]:
        if scene not in CAPTCHA_SCENE_SWITCHES:
            raise CaptchaInvalidError()
        if current_captcha_mode() != CAPTCHA_MODE_INTERACTIVE or not captcha_scene_enabled(scene):
            raise CaptchaInvalidError()
        types = enabled_captcha_types(scene)
        if not types:
            raise CaptchaInvalidError()
        selected = captcha_type or random.choice(types)
        backend = get_backend(selected) if selected in types else None
        if backend is None:
            raise CaptchaInvalidError()

        generated = backend.generate()
        challenge_id = uuid.uuid4().hex
        ttl = challenge_ttl()
        try:
            captcha_cache.store_challenge(
                challenge_id,
                {"type": generated.type, "scene": scene, "secret": generated.secret},
                ttl,
            )
        except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法出题（fail-closed）
            raise CaptchaInvalidError() from exc
        return {"challenge_id": challenge_id, "type": generated.type, "ttl": ttl, **generated.payload}

    def verify(
        self,
        challenge_id: str,
        captcha_type: str,
        scene: str,
        answer: Any,
        track: Any = None,
        ip: str = "",
    ) -> dict[str, Any]:
        if not captcha_globally_enabled():
            raise CaptchaInvalidError()
        try:
            stored = captcha_cache.load_challenge(challenge_id)
        except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法校验（fail-closed）
            raise CaptchaInvalidError() from exc
        if not stored or stored.get("type") != captcha_type or stored.get("scene") != scene:
            raise CaptchaInvalidError()

        backend = get_backend(captcha_type)
        if backend is None:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaInvalidError()

        try:
            attempts = captcha_cache.incr_attempt(challenge_id, challenge_ttl())
        except Exception as exc:  # noqa: BLE001 尝试计数不可用同样拒绝
            raise CaptchaInvalidError() from exc
        limit = max_attempts()
        if attempts > limit:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaExhaustedError()

        passed = backend.verify(stored.get("secret") or {}, answer)
        if passed and _track_required():
            min_points = 3 if captcha_type == CAPTCHA_TYPE_CLICK else 2
            passed = validate_track(track, min_points=min_points)
        if not passed:
            attempts_left = limit - attempts
            if attempts_left <= 0:
                captcha_cache.delete_challenge(challenge_id)
                raise CaptchaExhaustedError()
            return {"passed": False, "attempts_left": attempts_left}
        captcha_cache.delete_challenge(challenge_id)
        try:
            if not captcha_cache.reserve_ticket_slot(ip):
                raise CaptchaExhaustedError()
            ticket = captcha_cache.issue_ticket(scene, ticket_ttl())
        except CaptchaServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 ticket 写入失败 = 本次校验不可用（fail-closed）
            captcha_cache.release_ticket_slot(ip)
            raise CaptchaInvalidError() from exc
        return {"passed": True, "ticket": ticket, "expires_in": ticket_ttl()}

    def consume_ticket(self, ticket: str, scene: str, ip: str = "") -> bool:
        """一次性消费 ticket 并校验场景绑定；缓存故障按 fail-closed 拒绝。"""
        ticket = str(ticket or "").strip()
        if not ticket:
            return False
        try:
            passed = captcha_cache.consume_ticket(ticket, scene)
        except Exception:  # noqa: BLE001 缓存不可用 = 拒绝
            return False
        if ip:
            captcha_cache.release_ticket_slot(ip)
        return passed


def preview_image_captcha() -> dict[str, Any]:
    """按已保存的图片验证码配置生成一张预览图（不写 CaptchaStore）。"""
    from captcha.helpers import get_challenge
    from captcha.views import render_challenge

    challenge, response = get_challenge()()
    image = render_challenge(challenge, 1)
    return {"image": png_data_uri(image), "length": len(str(response))}


def preview_interactive_challenge(captcha_type: str) -> dict[str, Any]:
    """按已保存的交互式配置生成一题预览（不写挑战缓存、不签发 ticket）。"""
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


__all__ = [
    "CaptchaAuth",
    "CaptchaExhaustedError",
    "CaptchaInvalidError",
    "CaptchaServiceError",
    "InteractiveCaptcha",
    "build_captcha_meta",
    "enabled_captcha_types",
    "inspect_challenge",
    "preview_image_captcha",
    "preview_interactive_challenge",
]
