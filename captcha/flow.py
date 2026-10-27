#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""风险编排辅助：把信号 → 决策 → 题型/难度/票据档位的映射集中一处。

``InteractiveCaptcha`` 只负责 I/O（缓存、后端、票据）；本模块保持纯计算 +
少量缓存计数，便于单测逐条覆盖阈值、加权轮换与降级路径。
"""

import random
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.cache import cache

from captcha import risk
from captcha import signals as sig
from captcha.const import (
    CAPTCHA_DECISION_ALLOW,
    CAPTCHA_DECISION_CHALLENGE,
    CAPTCHA_DECISION_HARD,
    CAPTCHA_DECISION_LIGHT,
    CAPTCHA_DIFFICULTY_EASY,
    CAPTCHA_DIFFICULTY_HARD,
    CAPTCHA_DIFFICULTY_NORMAL,
    CAPTCHA_TYPE_CLICK,
    CAPTCHA_TYPE_ICON,
    CAPTCHA_TYPE_IMAGE,
    CAPTCHA_TYPE_POW,
    CAPTCHA_TYPE_WORDORDER,
    current_risk_mode,
)

RECENT_TYPE_PREFIX = "captcha_type_recent"
POW_BUDGET_PREFIX = "captcha_pow_budget"


@dataclass
class RiskContext:
    scene: str = "login"
    action: str = ""
    env: Any = None
    ip: str = ""
    ua: str = ""
    accept_language: str = ""
    sec_ch_ua: str = ""
    account: str = ""
    honeypot: bool = False
    xff_inconsistent: bool = False
    fp: str = field(default="")

    def __post_init__(self) -> None:
        if self.fp:
            return
        provided = self.env.get("fp") if isinstance(self.env, dict) else ""
        self.fp = sig.hash_value(str(provided)) if provided else sig.fingerprint(self.env, self.ua)

    @property
    def ip_hash(self) -> str:
        return sig.hash_ip(self.ip)


def risk_active() -> bool:
    """风险层是否运行：off 档完全不评估。"""
    return current_risk_mode() != "off"


def assess_challenge(ctx: RiskContext) -> risk.RiskResult | None:
    if not risk_active():
        return None
    return risk.assess(
        scene=ctx.scene,
        ip=ctx.ip,
        env=ctx.env,
        ua=ctx.ua,
        accept_language=ctx.accept_language,
        sec_ch_ua=ctx.sec_ch_ua,
        account=ctx.account,
        honeypot=ctx.honeypot,
        fp=ctx.fp,
        count_freq=True,
    )


def assess_verify(
    ctx: RiskContext, *, passed: bool, pow_ok: bool, track: Any, input_mode: str, captcha_type: str
) -> risk.RiskResult | None:
    if not risk_active():
        return None
    return risk.assess(
        scene=ctx.scene,
        ip=ctx.ip,
        env=ctx.env,
        ua=ctx.ua,
        accept_language=ctx.accept_language,
        sec_ch_ua=ctx.sec_ch_ua,
        account=ctx.account,
        honeypot=ctx.honeypot,
        fp=ctx.fp,
        track=track,
        input_mode=input_mode,
        pow_required=captcha_type == CAPTCHA_TYPE_POW,
        pow_ok=pow_ok if captcha_type == CAPTCHA_TYPE_POW else bool(passed),
        complex_type=captcha_type != CAPTCHA_TYPE_POW,
        count_freq=False,
    )


def stored_result(stored: dict[str, Any]) -> risk.RiskResult:
    """把挑战期决策还原为结果对象，用于与校验期评估取更严格者。"""
    decision = str(stored.get("decision") or CAPTCHA_DECISION_CHALLENGE)
    return risk.RiskResult(
        score=int(stored.get("risk") or 0),
        reasons=list(stored.get("risk_reasons") or []),
        decision=decision,
    )


def difficulty_for(decision: str) -> str:
    if decision == CAPTCHA_DECISION_HARD:
        return CAPTCHA_DIFFICULTY_HARD
    if decision == CAPTCHA_DECISION_LIGHT:
        return CAPTCHA_DIFFICULTY_EASY
    return CAPTCHA_DIFFICULTY_NORMAL


def ticket_mode_for(decision: str) -> str:
    if decision in (CAPTCHA_DECISION_ALLOW, CAPTCHA_DECISION_LIGHT, CAPTCHA_DECISION_HARD):
        return decision
    return "challenge"


def _weighted_types(types: list[str]) -> list[str]:
    weights = getattr(settings, "CAPTCHA_TYPE_WEIGHTS", None)
    if not isinstance(weights, dict):
        return types
    pool = [item for item in types if int(weights.get(item, 0) or 0) > 0]
    return pool or types


def _recent_type_key(ctx: RiskContext) -> str:
    return f"{RECENT_TYPE_PREFIX}_{ctx.fp or ctx.ip_hash or 'anonymous'}"


def _choose(ctx: RiskContext, pool: list[str]) -> str:
    """加权池内随机，并做短窗防重复；池为空时退回 click。"""
    if not pool:
        return CAPTCHA_TYPE_CLICK
    selected = random.choice(pool)
    try:
        recent = cache.get(_recent_type_key(ctx))
        if recent == selected and len(pool) > 1:
            selected = random.choice([item for item in pool if item != recent])
        cache.set(_recent_type_key(ctx), selected, 60)
    except Exception:  # noqa: BLE001 轮换缓存不可用不影响出题
        pass
    return selected


def _weight_positive(captcha_type: str) -> bool:
    weights = getattr(settings, "CAPTCHA_TYPE_WEIGHTS", None)
    if not isinstance(weights, dict):
        return True
    try:
        return int(weights.get(captcha_type, 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def pick_type(ctx: RiskContext, types: list[str], requested: str | None, decision: str) -> tuple[str, str]:
    """返回 (type, difficulty)。

    决策优先：allow/light 恒走 PoW；hard 忽略客户端请求并强制难题型；
    challenge 才允许在启用池内尊重请求；image 只作为显式回退且永不进 hard。
    """
    if decision in (CAPTCHA_DECISION_ALLOW, CAPTCHA_DECISION_LIGHT):
        # 显式图片回退优先于静默 PoW：无障碍用户需要看到图片题本身；
        # 图片题自带 PoW（CAPTCHA_POW_FOR_IMAGE_FALLBACK），不会白嫖。
        if requested == CAPTCHA_TYPE_IMAGE and bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", True)):
            return CAPTCHA_TYPE_IMAGE, difficulty_for(decision)
        if bool(getattr(settings, "CAPTCHA_POW_ENABLED", True)):
            difficulty = CAPTCHA_DIFFICULTY_EASY if decision == CAPTCHA_DECISION_ALLOW else CAPTCHA_DIFFICULTY_HARD
            return CAPTCHA_TYPE_POW, difficulty
        return CAPTCHA_TYPE_CLICK, CAPTCHA_DIFFICULTY_NORMAL

    candidates = [item for item in types if item != CAPTCHA_TYPE_POW]
    if decision == CAPTCHA_DECISION_HARD:
        hard_pool = [
            item for item in candidates if item in (CAPTCHA_TYPE_WORDORDER, CAPTCHA_TYPE_CLICK, CAPTCHA_TYPE_ICON)
        ]
        if CAPTCHA_TYPE_WORDORDER in hard_pool:
            return CAPTCHA_TYPE_WORDORDER, CAPTCHA_DIFFICULTY_HARD
        if hard_pool:
            return _choose(ctx, hard_pool), CAPTCHA_DIFFICULTY_HARD
        fallback = [item for item in candidates if item != CAPTCHA_TYPE_IMAGE]
        return _choose(ctx, fallback or [CAPTCHA_TYPE_CLICK]), CAPTCHA_DIFFICULTY_HARD

    if (
        requested == CAPTCHA_TYPE_IMAGE
        and bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", True))
        and CAPTCHA_TYPE_IMAGE in candidates
    ):
        return CAPTCHA_TYPE_IMAGE, difficulty_for(decision)

    if requested and requested in candidates and _weight_positive(requested):
        return requested, difficulty_for(decision)

    pool = _weighted_types(candidates)
    if not pool:
        pool = [item for item in candidates if item != CAPTCHA_TYPE_IMAGE] or [CAPTCHA_TYPE_CLICK]
    return _choose(ctx, pool), difficulty_for(decision)


def pow_budget_ok(ip_hash: str) -> bool:
    """单 IP 每小时 PoW 预算（防被拿去做算力放大器）。"""
    if not ip_hash:
        return True
    budget = int(getattr(settings, "CAPTCHA_POW_IP_BUDGET", 600) or 0)
    if budget <= 0:
        return True
    key = f"{POW_BUDGET_PREFIX}_{ip_hash}"
    try:
        count = int(cache.get(key, 0) or 0) + 1
        cache.set(key, count, 3600)
        return count <= budget
    except Exception:  # noqa: BLE001 缓存不可用 = 不拦截（宁可放行也不误伤）
        return True


__all__ = [
    "RiskContext",
    "assess_challenge",
    "assess_verify",
    "difficulty_for",
    "pick_type",
    "pow_budget_ok",
    "risk_active",
    "stored_result",
    "ticket_mode_for",
]
