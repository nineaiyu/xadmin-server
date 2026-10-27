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

import time
import uuid
from typing import Any

from django.conf import settings

from captcha import cache as captcha_cache
from captcha import disposal, flow, risk, telemetry, tickets
from captcha import signals as sig
from captcha.backends import get_backend
from captcha.backends.track import validate_track
from captcha.challenge_flow import early_decision, emit_decision, emit_verify, failure_dims
from captcha.const import (
    CAPTCHA_DECISION_CHALLENGE,
    CAPTCHA_DECISION_DENY,
    CAPTCHA_DECISION_HARD,
    CAPTCHA_DECISION_LIGHT,
    CAPTCHA_MODE_INTERACTIVE,
    CAPTCHA_SCENE_SWITCHES,
    CAPTCHA_TYPE_CLICK,
    CAPTCHA_TYPE_POW,
    captcha_globally_enabled,
    captcha_scene_enabled,
    current_captcha_mode,
    current_risk_mode,
)
from captcha.errors import CaptchaExhaustedError, CaptchaInvalidError, CaptchaServiceError
from captcha.meta import (
    build_captcha_meta,
    challenge_ttl,
    enabled_captcha_types,
    inspect_challenge,
    max_attempts,
    preview_image_captcha,
    preview_interactive_challenge,
    track_required,
)
from captcha.utils import CaptchaAuth


def _load_stored(challenge_id: str, captcha_type: str, scene: str) -> dict[str, Any]:
    try:
        stored = captcha_cache.load_challenge(challenge_id)
    except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法校验（fail-closed）
        raise CaptchaInvalidError() from exc
    if not stored or stored.get("type") != captcha_type or stored.get("scene") != scene:
        raise CaptchaInvalidError()
    return stored


def _attempts(challenge_id: str) -> int:
    try:
        attempts = captcha_cache.incr_attempt(challenge_id, challenge_ttl())
    except Exception as exc:  # noqa: BLE001 尝试计数不可用同样拒绝
        raise CaptchaInvalidError() from exc
    if attempts > max_attempts():
        captcha_cache.delete_challenge(challenge_id)
        raise CaptchaExhaustedError()
    return attempts


def _answer_passed(
    backend: Any,
    stored: dict[str, Any],
    answer: Any,
    captcha_type: str,
    difficulty: str,
    track: Any,
    input_mode: str,
) -> bool:
    passed = bool(backend.verify(stored.get("secret") or {}, answer, difficulty=difficulty))
    if passed and track_required() and captcha_type != CAPTCHA_TYPE_POW and input_mode != "keyboard":
        min_points = 3 if captcha_type == CAPTCHA_TYPE_CLICK else 2
        passed = bool(validate_track(track, min_points=min_points))
    return passed


def _build_payload(
    ctx: flow.RiskContext,
    *,
    scene: str,
    action: str,
    generated: Any,
    difficulty: str,
    decision: str,
    assessed: risk.RiskResult | None,
    enforce: bool,
) -> dict[str, Any]:
    return {
        "type": generated.type,
        "scene": scene,
        "secret": generated.secret,
        "action": action,
        "decision": decision if enforce else "",
        "risk": assessed.score if assessed else 0,
        "risk_reasons": assessed.reasons if assessed else [],
        "fp": ctx.fp,
        "ip_hash": ctx.ip_hash,
        "difficulty": difficulty,
    }


class InteractiveCaptcha:
    """交互式验证码编排：生成挑战 -> 服务端校验 -> 签发一次性签名 ticket。"""

    def challenge(
        self,
        captcha_type: str | None = None,
        scene: str = "login",
        action: str = "",
        env: Any = None,
        ip: str = "",
        ua: str = "",
        accept_language: str = "",
        sec_ch_ua: str = "",
        account: str = "",
        honeypot: bool = False,
        xff_inconsistent: bool = False,
    ) -> dict[str, Any]:
        if scene not in CAPTCHA_SCENE_SWITCHES:
            raise CaptchaInvalidError()
        if current_captcha_mode() != CAPTCHA_MODE_INTERACTIVE or not captcha_scene_enabled(scene):
            raise CaptchaInvalidError()
        if action and not tickets.action_allowed_for_scene(scene, action):
            raise CaptchaInvalidError()
        types = enabled_captcha_types(scene)
        if not types or (captcha_type and captcha_type not in types):
            raise CaptchaInvalidError()

        ctx = flow.RiskContext(
            scene=scene,
            action=action,
            env=env,
            ip=ip,
            ua=ua,
            accept_language=accept_language,
            sec_ch_ua=sec_ch_ua,
            account=account,
            honeypot=honeypot,
            xff_inconsistent=xff_inconsistent,
        )
        assessed = flow.assess_challenge(ctx)
        enforce = current_risk_mode() == "enforce" and assessed is not None
        # 失败阶梯第一档：连续失败达阈值后，下一题强制难题型（只升不降；信任网段不参与）
        if (
            enforce
            and assessed is not None
            and not assessed.trusted
            and disposal.forced_hard(failure_dims(ctx, action))
        ):
            if risk.severity(assessed.decision) < risk.severity(CAPTCHA_DECISION_HARD):
                assessed.decision = CAPTCHA_DECISION_HARD
                assessed.reasons.append("fail_ladder")
        early = early_decision(ctx, assessed, scene, action)
        if early is not None:
            return early
        decision = assessed.decision if enforce and assessed else CAPTCHA_DECISION_CHALLENGE
        if decision == CAPTCHA_DECISION_LIGHT and not bool(getattr(settings, "CAPTCHA_POW_ENABLED", True)):
            decision = CAPTCHA_DECISION_CHALLENGE
        selected, difficulty = flow.pick_type(ctx, types, captcha_type, decision)
        backend = get_backend(selected)
        if backend is None:
            raise CaptchaInvalidError()

        generated = backend.generate(difficulty)
        challenge_id = uuid.uuid4().hex
        ttl = challenge_ttl()
        payload = _build_payload(
            ctx,
            scene=scene,
            action=action,
            generated=generated,
            difficulty=difficulty,
            decision=decision,
            assessed=assessed,
            enforce=enforce,
        )
        ck = ""
        if tickets.verify_sign_enforced():
            ck = tickets.new_session_key()
            payload["ck"] = ck
        try:
            captcha_cache.store_challenge(challenge_id, payload, ttl)
        except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法出题（fail-closed）
            raise CaptchaInvalidError() from exc
        return self._challenge_result(challenge_id, generated, ttl, ck, enforce, decision, difficulty, ctx, assessed)

    def _challenge_result(
        self,
        challenge_id: str,
        generated: Any,
        ttl: int,
        ck: str,
        enforce: bool,
        decision: str,
        difficulty: str,
        ctx: flow.RiskContext,
        assessed: risk.RiskResult | None,
    ) -> dict[str, Any]:
        if generated.type == CAPTCHA_TYPE_POW:
            result: dict[str, Any] = {
                "challenge_id": challenge_id,
                "type": generated.type,
                "ttl": ttl,
                "pow": generated.payload,
            }
        else:
            result = {"challenge_id": challenge_id, "type": generated.type, "ttl": ttl, **generated.payload}
        if ck:
            result["ck"] = ck
        if enforce:
            result["decision"] = decision
            result["difficulty"] = difficulty
        if assessed is not None:
            emit_decision(ctx, assessed, phase="challenge", captcha_type=generated.type, difficulty=difficulty)
        else:
            telemetry.emit(
                phase="challenge", scene=ctx.scene, action=ctx.action, mode=current_risk_mode(), type=generated.type
            )
        return result

    def verify(
        self,
        challenge_id: str,
        captcha_type: str,
        scene: str,
        answer: Any,
        track: Any = None,
        ip: str = "",
        action: str = "",
        gen_time: Any = None,
        nonce: Any = None,
        sign: Any = None,
        ua: str = "",
        env: Any = None,
        pow_answer: Any = None,
        input_mode: str = "",
        honeypot: bool = False,
        account: str = "",
        accept_language: str = "",
        sec_ch_ua: str = "",
        xff_inconsistent: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        if not self._validate_action(scene, action):
            raise CaptchaInvalidError()
        stored = _load_stored(challenge_id, captcha_type, scene)
        backend = get_backend(captcha_type)
        if backend is None:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaInvalidError()
        effective_answer = pow_answer if captcha_type == CAPTCHA_TYPE_POW and pow_answer is not None else answer
        attempts = _attempts(challenge_id)
        if tickets.verify_sign_enforced() and not tickets.verify_request_signature(
            str(stored.get("ck") or ""), challenge_id, captcha_type, scene, effective_answer, gen_time, nonce, sign
        ):
            raise CaptchaInvalidError()
        difficulty = str(stored.get("difficulty") or "normal")
        passed = _answer_passed(backend, stored, effective_answer, captcha_type, difficulty, track, input_mode)
        ctx = flow.RiskContext(
            scene=scene,
            action=action,
            env=env,
            ip=ip,
            ua=ua,
            accept_language=accept_language,
            sec_ch_ua=sec_ch_ua,
            account=account,
            honeypot=honeypot,
            xff_inconsistent=xff_inconsistent,
        )
        verify_result = flow.assess_verify(
            ctx, passed=passed, pow_ok=passed, track=track, input_mode=input_mode, captcha_type=captcha_type
        )
        merged = risk.stricter(flow.stored_result(stored), verify_result) if verify_result else None
        enforce = current_risk_mode() == "enforce" and merged is not None
        dims = failure_dims(ctx, action)
        failure = self._verify_failure_reason(enforce, merged, dims, ctx, captcha_type)
        emit_verify(ctx, stored, attempts, started, passed, merged, failure)
        if failure or not passed:
            return self._verify_failed(
                challenge_id,
                scene=scene,
                ip=ip,
                action=action,
                account=account,
                ctx=ctx,
                dims=dims,
                attempts=attempts,
                limit=max_attempts(),
            )
        return self._verify_passed(
            challenge_id,
            ctx=ctx,
            scene=scene,
            action=action,
            captcha_type=captcha_type,
            ip=ip,
            merged=merged,
            enforce=enforce,
        )

    @staticmethod
    def _validate_action(scene: str, action: str) -> bool:
        if not captcha_globally_enabled():
            return False
        if tickets.action_binding_enabled():
            return bool(action) and tickets.action_allowed_for_scene(scene, action)
        return not action or tickets.action_allowed_for_scene(scene, action)

    @staticmethod
    def _verify_failure_reason(
        enforce: bool,
        merged: risk.RiskResult | None,
        dims: list[tuple[str, str]],
        ctx: flow.RiskContext,
        captcha_type: str,
    ) -> str:
        if not enforce:
            return ""
        from captcha import disposal

        if disposal.cooldown_remaining(dims):
            return "cooldown"
        if captcha_type == CAPTCHA_TYPE_POW and not flow.pow_budget_ok(ctx.ip_hash):
            return "pow_budget"
        if merged is not None and merged.decision == CAPTCHA_DECISION_DENY:
            return "deny"
        return ""

    def _verify_failed(
        self,
        challenge_id: str,
        *,
        scene: str,
        ip: str,
        action: str,
        account: str,
        ctx: flow.RiskContext,
        dims: list[tuple[str, str]],
        attempts: int,
        limit: int,
    ) -> dict[str, Any]:
        from captcha import disposal

        disposal.register_failure(dims)
        disposal.link_login_lock(account, ip)
        sig.record_account_fail(account)
        sig.record_hist(ctx.fp, False)
        attempts_left = max(limit - attempts, 0)
        if attempts_left <= 0:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaExhaustedError()
        return {"passed": False, "attempts_left": attempts_left}

    def _verify_passed(
        self,
        challenge_id: str,
        *,
        ctx: flow.RiskContext,
        scene: str,
        action: str,
        captcha_type: str,
        ip: str,
        merged: risk.RiskResult | None,
        enforce: bool,
    ) -> dict[str, Any]:
        from captcha import disposal

        disposal.clear_failures(failure_dims(ctx, action))
        sig.record_hist(ctx.fp, True)
        captcha_cache.delete_challenge(challenge_id)
        decision = merged.decision if enforce and merged else CAPTCHA_DECISION_CHALLENGE
        ticket_mode = flow.ticket_mode_for(decision)
        ttl = tickets.ticket_ttl_for(ticket_mode)
        try:
            if not captcha_cache.reserve_ticket_slot(ip):
                raise CaptchaExhaustedError()
            ticket = tickets.issue_ticket(
                scene=scene,
                action=action,
                mode=ticket_mode,
                captcha_type=captcha_type,
                ttl=ttl,
                fp=ctx.fp,
                ip_hash=ctx.ip_hash,
            )
        except CaptchaServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 ticket 写入失败 = 本次校验不可用（fail-closed）
            captcha_cache.release_ticket_slot(ip)
            raise CaptchaInvalidError() from exc
        return {"passed": True, "ticket": ticket, "expires_in": ttl}

    def consume_ticket(self, ticket: str, scene: str, ip: str = "", action: str = "", fp: str = "") -> bool:
        """一次性消费签名 ticket 并校验 (scene, action) 绑定；缓存故障按 fail-closed 拒绝。"""
        ticket = str(ticket or "").strip()
        if not ticket:
            return False
        try:
            payload = tickets.consume_ticket_payload(ticket, scene, action=action, ip_hash=sig.hash_ip(ip))
        except Exception:  # noqa: BLE001 缓存不可用 = 拒绝
            return False
        passed = payload is not None
        telemetry.emit(
            phase="consume",
            scene=scene,
            action=action,
            mode=current_risk_mode(),
            passed=passed,
            ip_hash=sig.hash_ip(ip),
            key_id=str((payload or {}).get("kid") or ""),
        )
        if ip:
            captcha_cache.release_ticket_slot(ip)
        return passed


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
