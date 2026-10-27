#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""挑战/校验期的决策留痕与提前决策（allow/deny/冷却）。

从 ``services.py`` 拆出：保持编排文件在行数门禁内，决策副作用集中一处。
"""

import time
from typing import Any

from captcha import alerts, disposal, flow, risk, telemetry, tickets
from captcha import cache as captcha_cache
from captcha import signals as sig
from captcha.const import CAPTCHA_DECISION_ALLOW, CAPTCHA_DECISION_DENY
from captcha.errors import CaptchaExhaustedError, CaptchaInvalidError


def failure_dims(ctx: flow.RiskContext, action: str) -> list[tuple[str, str]]:
    return disposal.dimension_keys(ip_hash=ctx.ip_hash, action=action, fp=ctx.fp, account=sig.hash_value(ctx.account))


def emit_decision(
    ctx: flow.RiskContext,
    result: risk.RiskResult,
    *,
    phase: str,
    captcha_type: str = "",
    difficulty: str = "",
    passed: bool = False,
) -> None:
    from captcha.const import current_risk_mode

    telemetry.emit(
        phase=phase,
        scene=ctx.scene,
        action=ctx.action,
        mode=current_risk_mode(),
        type=captcha_type,
        difficulty=difficulty,
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


def early_decision(
    ctx: flow.RiskContext, assessed: risk.RiskResult | None, scene: str, action: str
) -> dict[str, Any] | None:
    """enforce 下处理冷却 / deny / allow；非提前返回场景返回 None。"""
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
    if assessed.decision != CAPTCHA_DECISION_ALLOW:
        return None
    ttl = tickets.ticket_ttl_for("allow")
    # 无题直过也必须受「同 IP 未消费票据 ≤3」约束，防低风险票被批量囤积
    if not captcha_cache.reserve_ticket_slot(ctx.ip):
        raise CaptchaExhaustedError()
    try:
        ticket = tickets.issue_ticket(scene=scene, action=action, mode="allow", ttl=ttl, fp=ctx.fp, ip_hash=ctx.ip_hash)
    except Exception:
        captcha_cache.release_ticket_slot(ctx.ip)
        raise
    emit_decision(ctx, assessed, phase="decision", passed=True)
    telemetry.emit(
        phase="challenge",
        scene=scene,
        action=action,
        mode=current_risk_mode(),
        type="",
        decision=assessed.decision,
        risk_score=assessed.score,
        risk_reasons=assessed.reasons,
        fp_hash=ctx.fp,
        ip_hash=ctx.ip_hash,
        env_version=sig.env_version(ctx.env),
    )
    return flow.allow_response(ticket, ttl)


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
