#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码的题型、场景与模式常量。

题型注册表在 ``captcha.backends``，本模块只放稳定的字符串事实，
供视图、序列化器、能力清单与业务消费方共享，避免各处散落字面量。
"""

from django.conf import settings

CAPTCHA_MODE_IMAGE = "image"
CAPTCHA_MODE_INTERACTIVE = "interactive"
CAPTCHA_MODES = (CAPTCHA_MODE_IMAGE, CAPTCHA_MODE_INTERACTIVE)

CAPTCHA_TYPE_SLIDE = "slide"
CAPTCHA_TYPE_ROTATE = "rotate"
CAPTCHA_TYPE_CLICK = "click"
CAPTCHA_TYPES = (CAPTCHA_TYPE_SLIDE, CAPTCHA_TYPE_ROTATE, CAPTCHA_TYPE_CLICK)

CAPTCHA_SCENE_LOGIN = "login"
CAPTCHA_SCENE_REGISTER = "register"
CAPTCHA_SCENE_RESET = "reset"
CAPTCHA_SCENE_BIND_EMAIL = "bind_email"
CAPTCHA_SCENE_BIND_PHONE = "bind_phone"
CAPTCHA_SCENE_INVITE = "invite"
CAPTCHA_SCENES = (
    CAPTCHA_SCENE_LOGIN,
    CAPTCHA_SCENE_REGISTER,
    CAPTCHA_SCENE_RESET,
    CAPTCHA_SCENE_BIND_EMAIL,
    CAPTCHA_SCENE_BIND_PHONE,
    CAPTCHA_SCENE_INVITE,
)

#: 场景 -> 是否启用验证码的开关名。邀请激活没有独立开关（匿名激活必须过人机校验）。
CAPTCHA_SCENE_SWITCHES = {
    CAPTCHA_SCENE_LOGIN: "SECURITY_LOGIN_CAPTCHA_ENABLED",
    CAPTCHA_SCENE_REGISTER: "SECURITY_REGISTER_CAPTCHA_ENABLED",
    CAPTCHA_SCENE_RESET: "SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED",
    CAPTCHA_SCENE_BIND_EMAIL: "SECURITY_BIND_EMAIL_CAPTCHA_ENABLED",
    CAPTCHA_SCENE_BIND_PHONE: "SECURITY_BIND_PHONE_CAPTCHA_ENABLED",
    CAPTCHA_SCENE_INVITE: None,
}


def current_captcha_mode() -> str:
    """当前出题模式：非法/空值回退 image（升级零行为变化）。"""
    mode = str(getattr(settings, "CAPTCHA_MODE", CAPTCHA_MODE_IMAGE) or "").strip().lower()
    return mode if mode in CAPTCHA_MODES else CAPTCHA_MODE_IMAGE


def captcha_globally_enabled() -> bool:
    """验证码总开关（默认开启，升级零行为变化）。"""
    return bool(getattr(settings, "CAPTCHA_ENABLED", True))


def captcha_scene_enabled(scene: str) -> bool:
    """场景级验证码开关：总开关 AND 场景开关；未知场景 fail-closed。"""
    if not captcha_globally_enabled():
        return False
    switch = CAPTCHA_SCENE_SWITCHES.get(scene)
    if scene not in CAPTCHA_SCENE_SWITCHES:
        return False
    if switch is None:
        return True
    return bool(getattr(settings, switch, False))
