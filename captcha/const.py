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
CAPTCHA_TYPE_POW = "pow"
CAPTCHA_TYPE_WORDORDER = "wordorder"
CAPTCHA_TYPES = (
    CAPTCHA_TYPE_SLIDE,
    CAPTCHA_TYPE_ROTATE,
    CAPTCHA_TYPE_CLICK,
    CAPTCHA_TYPE_POW,
    CAPTCHA_TYPE_WORDORDER,
)
#: 可参与常规出题的题型（pow 由决策层按档位驱动，不在随机池里）
CAPTCHA_PUZZLE_TYPES = (CAPTCHA_TYPE_SLIDE, CAPTCHA_TYPE_ROTATE, CAPTCHA_TYPE_CLICK, CAPTCHA_TYPE_WORDORDER)

CAPTCHA_DIFFICULTY_EASY = "easy"
CAPTCHA_DIFFICULTY_NORMAL = "normal"
CAPTCHA_DIFFICULTY_HARD = "hard"
CAPTCHA_DIFFICULTIES = (CAPTCHA_DIFFICULTY_EASY, CAPTCHA_DIFFICULTY_NORMAL, CAPTCHA_DIFFICULTY_HARD)

CAPTCHA_DECISION_ALLOW = "allow"
CAPTCHA_DECISION_LIGHT = "light"
CAPTCHA_DECISION_CHALLENGE = "challenge"
CAPTCHA_DECISION_HARD = "hard"
CAPTCHA_DECISION_DENY = "deny"
CAPTCHA_DECISIONS = (
    CAPTCHA_DECISION_ALLOW,
    CAPTCHA_DECISION_LIGHT,
    CAPTCHA_DECISION_CHALLENGE,
    CAPTCHA_DECISION_HARD,
    CAPTCHA_DECISION_DENY,
)

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


#: 风险分级三档：off = 升级零行为变化（默认）；shadow = 跑分留痕不改变放行；
#: enforce = 按风险决策分级验证（信号/规则层上线后启用）。
CAPTCHA_RISK_MODE_OFF = "off"
CAPTCHA_RISK_MODE_SHADOW = "shadow"
CAPTCHA_RISK_MODE_ENFORCE = "enforce"
CAPTCHA_RISK_MODES = (CAPTCHA_RISK_MODE_OFF, CAPTCHA_RISK_MODE_SHADOW, CAPTCHA_RISK_MODE_ENFORCE)

#: 业务动作常量（服务端单源）：票据按 (scene, action) 绑定，消费点必须引用
#: 常量而非字面量。action 由服务端消费点声明，前端业务请求不携带。
CAPTCHA_ACTION_LOGIN = "identity.login.basic"
CAPTCHA_ACTION_REGISTER = "identity.register"
CAPTCHA_ACTION_RESET_PASSWORD = "identity.reset_password"
CAPTCHA_ACTION_BIND_EMAIL = "identity.bind_email"
CAPTCHA_ACTION_BIND_PHONE = "identity.bind_phone"
CAPTCHA_ACTION_INVITE_ACCEPT = "invite.accept"
CAPTCHA_ACTION_VERIFY_CODE_SEND_PREFIX = "auth.verify_code.send:"


def verify_code_send_action(category: str) -> str:
    """发码动作常量：按场景派生（一个 category 一条 action）。"""
    return f"{CAPTCHA_ACTION_VERIFY_CODE_SEND_PREFIX}{category}"


#: scene -> 该场景下合法的业务动作集合（票据签发时校验声明值）。
#: 登录场景双入口（账密登录 / 登录发码）共用 scene=login，靠 action 区分，
#: 消灭「用登录票去发短信验证码」这类票据挪用。
CAPTCHA_SCENE_ACTIONS: dict[str, tuple[str, ...]] = {
    CAPTCHA_SCENE_LOGIN: (
        CAPTCHA_ACTION_LOGIN,
        verify_code_send_action(CAPTCHA_SCENE_LOGIN),
    ),
    CAPTCHA_SCENE_REGISTER: (verify_code_send_action(CAPTCHA_SCENE_REGISTER),),
    CAPTCHA_SCENE_RESET: (verify_code_send_action(CAPTCHA_SCENE_RESET),),
    CAPTCHA_SCENE_BIND_EMAIL: (verify_code_send_action(CAPTCHA_SCENE_BIND_EMAIL),),
    CAPTCHA_SCENE_BIND_PHONE: (verify_code_send_action(CAPTCHA_SCENE_BIND_PHONE),),
    CAPTCHA_SCENE_INVITE: (CAPTCHA_ACTION_INVITE_ACCEPT,),
}


def current_captcha_mode() -> str:
    """当前出题模式：非法/空值回退 image（升级零行为变化）。"""
    mode = str(getattr(settings, "CAPTCHA_MODE", CAPTCHA_MODE_IMAGE) or "").strip().lower()
    return mode if mode in CAPTCHA_MODES else CAPTCHA_MODE_IMAGE


def current_risk_mode() -> str:
    """当前风险分级模式：非法/空值回退 off（升级零行为变化）。"""
    mode = str(getattr(settings, "CAPTCHA_RISK_MODE", CAPTCHA_RISK_MODE_OFF) or "").strip().lower()
    return mode if mode in CAPTCHA_RISK_MODES else CAPTCHA_RISK_MODE_OFF


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
