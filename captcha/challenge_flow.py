#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""挑战/校验期的决策留痕与提前决策（allow/deny/冷却）。

从 ``services.py`` 拆出：保持编排文件在行数门禁内，决策副作用集中一处。
"""

import time
from typing import Any

from django.conf import settings

from captcha import alerts, disposal, flow, risk, telemetry, tickets
from captcha import cache as captcha_cache
from captcha import signals as sig
from captcha.backends.track import validate_track
from captcha.const import CAPTCHA_DECISION_DENY, CAPTCHA_TYPE_CLICK, CAPTCHA_TYPE_IMAGE, CAPTCHA_TYPE_POW
from captcha.errors import CaptchaInvalidError
from captcha.meta import track_required


def strict_binding() -> bool:
    return bool(getattr(settings, "CAPTCHA_STRICT_BINDING", True))


def load_stored_challenge(
    challenge_id: str, captcha_type: str, scene: str, sid: str = "", action: str = ""
) -> dict[str, Any]:
    """读取挑战并按类型/场景/动作/sid 绑定校验；任一不符统一按非法挑战拒绝。"""
    try:
        stored = captcha_cache.load_challenge(challenge_id)
    except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法校验（fail-closed）
        raise CaptchaInvalidError() from exc
    if not stored or stored.get("type") != captcha_type or stored.get("scene") != scene:
        raise CaptchaInvalidError()
    # 题面声明的 action 必须贯穿到校验：否则同 scene 内可用 A 动作出的题换 B 动作的票据
    stored_action = str(stored.get("action") or "")
    if tickets.action_binding_enabled() and stored_action and stored_action != action:
        raise CaptchaInvalidError()
    if strict_binding():
        expected = str(stored.get("sid_hash") or "")
        if not sid or not expected or expected != sig.hash_value(sid):
            raise CaptchaInvalidError()
    return stored


def failure_dims(ctx: flow.RiskContext, action: str) -> list[tuple[str, str]]:
    return disposal.dimension_keys(ip_hash=ctx.ip_hash, action=action, fp=ctx.fp, account=sig.hash_value(ctx.account))


def answer_passed(
    backend: Any,
    stored: dict[str, Any],
    answer: Any,
    captcha_type: str,
    difficulty: str,
    track: Any,
    input_mode: str,
) -> bool:
    """答案校验 + 轨迹门槛；点选题的最小采样点数与目标数一致（easy 也可 2 点）。"""
    passed = bool(backend.verify(stored.get("secret") or {}, answer, difficulty=difficulty))
    if (
        passed
        and track_required()
        and captcha_type not in (CAPTCHA_TYPE_POW, CAPTCHA_TYPE_IMAGE)
        and input_mode != "keyboard"
    ):
        expected = (stored.get("secret") or {}).get("points")
        if isinstance(expected, list) and expected:
            min_points = max(len(expected), 2)
        else:
            min_points = 3 if captcha_type == CAPTCHA_TYPE_CLICK else 2
        passed = bool(validate_track(track, min_points=min_points))
    return passed


def emit_decision(
    ctx: flow.RiskContext,
    result: risk.RiskResult,
    *,
    phase: str,
    captcha_type: str = "",
    difficulty: str = "",
    passed: bool = False,
    asset_id: str = "",
) -> None:
    from captcha.const import current_risk_mode

    telemetry.emit(
        phase=phase,
        scene=ctx.scene,
        action=ctx.action,
        mode=current_risk_mode(),
        type=captcha_type,
        difficulty=difficulty,
        asset_id=asset_id,
        decision=result.decision,
        would_decision=result.decision if current_risk_mode() == "shadow" else "",
        risk_score=result.score,
        risk_reasons=result.reasons,
        passed=passed,
        ip_hash=ctx.ip_hash,
        fp_hash=ctx.fp,
        ua_class=telemetry.classify_ua(ctx.ua),
        browser=telemetry.browser_family(ctx.ua),
        env_version=sig.env_version(ctx.env),
    )
    alerts.record_decision(result.decision, result.reasons)
    if "freq_ip" in result.reasons:
        alerts.record_ip_spike(ctx.ip_hash)


def early_decision(ctx: flow.RiskContext, assessed: risk.RiskResult | None, scene: str, action: str) -> None:
    """enforce 下处理冷却 / deny；allow 走静默 PoW，不在此提前发票。

    只有两种结果：命中即抛 ``CaptchaInvalidError``，否则正常返回（不存在
    「提前返回响应」的分支）。
    """
    from captcha.const import current_risk_mode

    if current_risk_mode() != "enforce" or assessed is None:
        return None
    if disposal.cooldown_remaining(failure_dims(ctx, action)):
        denied = risk.RiskResult(score=100, reasons=["cooldown"], decision=CAPTCHA_DECISION_DENY)
        emit_decision(ctx, denied, phase="decision")
        raise CaptchaInvalidError()
    if assessed.decision == CAPTCHA_DECISION_DENY:
        emit_decision(ctx, assessed, phase="decision")
        raise CaptchaInvalidError()
    return None


def emit_verify(
    ctx: flow.RiskContext,
    stored: dict[str, Any],
    attempts: int,
    started: float,
    passed: bool,
    merged: risk.RiskResult | None,
    failure: str,
) -> None:
    from captcha.const import CAPTCHA_DECISION_DENY, CAPTCHA_TYPE_POW, current_risk_mode

    mode = current_risk_mode()
    decision = merged.decision if (merged and mode == "enforce") else ""
    if failure:
        decision = CAPTCHA_DECISION_DENY
    telemetry.emit(
        phase="verify",
        scene=ctx.scene,
        action=ctx.action,
        mode=mode,
        type=stored.get("type") or "",
        difficulty=stored.get("difficulty") or "",
        asset_id=str(stored.get("asset_id") or ""),
        decision=decision,
        would_decision=merged.decision if (merged and mode == "shadow") else "",
        risk_score=merged.score if merged else None,
        risk_reasons=merged.reasons if merged else [],
        passed=passed and not failure,
        attempts=attempts,
        latency_ms=max(1, round((time.monotonic() - started) * 1000)),
        ip_hash=ctx.ip_hash,
        fp_hash=ctx.fp,
        ua_class=telemetry.classify_ua(ctx.ua),
        browser=telemetry.browser_family(ctx.ua),
        env_version=sig.env_version(ctx.env),
        pow_bits=(stored.get("secret") or {}).get("bits") if stored.get("type") == CAPTCHA_TYPE_POW else None,
    )
    if merged is not None:
        alerts.record_decision(merged.decision, merged.reasons)
        if "freq_ip" in merged.reasons:
            alerts.record_ip_spike(ctx.ip_hash)


__all__ = ["early_decision", "emit_decision", "emit_verify", "failure_dims"]
