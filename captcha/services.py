#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""captcha app 对外服务契约层。

其他 app 需要使用验证码能力时，只允许从本模块导入：

- ``InteractiveCaptcha``：统一题型编排（挑战 / 校验 / ticket 消费 / 音频）；
- ``build_captcha_meta`` / ``enabled_captcha_types``：能力清单投影。

禁止直接 import ``captcha.backends`` 等内部实现，避免 app 间横向依赖扩散。
"""

import secrets
import time
import uuid
from typing import Any

from django.conf import settings

from captcha import cache as captcha_cache
from captcha import disposal, flow, risk, telemetry, tickets
from captcha import signals as sig
from captcha.backends import get_backend
from captcha.challenge_flow import (
    answer_passed,
    early_decision,
    emit_decision,
    emit_verify,
    failure_dims,
    load_stored_challenge,
    strict_binding,
)
from captcha.const import (
    CAPTCHA_DECISION_CHALLENGE,
    CAPTCHA_DECISION_DENY,
    CAPTCHA_DECISION_HARD,
    CAPTCHA_DECISION_LIGHT,
    CAPTCHA_SCENE_SWITCHES,
    CAPTCHA_TYPE_IMAGE,
    CAPTCHA_TYPE_POW,
    captcha_globally_enabled,
    captcha_scene_enabled,
    current_risk_mode,
)
from captcha.errors import CaptchaInvalidError, CaptchaServiceError
from captcha.meta import (
    build_captcha_meta,
    challenge_ttl,
    enabled_captcha_types,
    max_attempts,
    preview_image_captcha,
    preview_interactive_challenge,
)


def _attempts(challenge_id: str) -> int:
    try:
        attempts = captcha_cache.incr_attempt(challenge_id, challenge_ttl())
    except Exception as exc:  # noqa: BLE001 尝试计数不可用同样拒绝
        raise CaptchaInvalidError() from exc
    if attempts > max_attempts():
        captcha_cache.delete_challenge(challenge_id)
        raise CaptchaInvalidError()
    return attempts


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
    sid_hash: str = "",
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
        "sid_hash": sid_hash,
        "asset_id": str((generated.secret or {}).get("asset_id") or ""),
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
        sid: str = "",
    ) -> dict[str, Any]:
        if scene not in CAPTCHA_SCENE_SWITCHES:
            raise CaptchaInvalidError()
        if not captcha_globally_enabled() or not captcha_scene_enabled(scene):
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
        early_decision(ctx, assessed, scene, action)
        decision = assessed.decision if enforce and assessed else CAPTCHA_DECISION_CHALLENGE
        if decision == CAPTCHA_DECISION_LIGHT and not bool(getattr(settings, "CAPTCHA_POW_ENABLED", True)):
            decision = CAPTCHA_DECISION_CHALLENGE
        selected, difficulty = flow.pick_type(ctx, types, captcha_type, decision)
        selected = flow.avoid_pow_budget(ctx.ip_hash, types, selected)
        backend = get_backend(selected)
        if backend is None:
            raise CaptchaInvalidError()

        generated = backend.generate(difficulty)
        challenge_id = uuid.uuid4().hex
        ttl = challenge_ttl()
        # 会话 sid 在同一浏览器内复用：每个挑战都换新 cookie 会让多标签页
        # 互相顶掉对方，先出的题因 sid 不再匹配而永远无法校验。
        session_id = str(sid or "").strip()
        if not 16 <= len(session_id) <= 128:
            session_id = secrets.token_urlsafe(32)
        payload = _build_payload(
            ctx,
            scene=scene,
            action=action,
            generated=generated,
            difficulty=difficulty,
            decision=decision,
            assessed=assessed,
            enforce=enforce,
            sid_hash=sig.hash_value(session_id),
        )
        ck = ""
        if tickets.verify_sign_enforced():
            ck = tickets.new_session_key()
            payload["ck"] = ck
        try:
            captcha_cache.store_challenge(challenge_id, payload, ttl)
        except Exception as exc:  # noqa: BLE001 缓存不可用 = 无法出题（fail-closed）
            raise CaptchaInvalidError() from exc
        sig.record_challenge(ip, fp=ctx.fp, captcha_type=generated.type)
        result = self._challenge_result(challenge_id, generated, ttl, ck, enforce, decision, difficulty, ctx, assessed)
        # 服务端时间：verify 加签以它为时间基准，客户端时钟偏差不再导致必败
        result["ts"] = int(time.time())
        result["_sid"] = session_id
        return result

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
        if assessed is not None:
            emit_decision(
                ctx,
                assessed,
                phase="challenge",
                captcha_type=generated.type,
                difficulty=difficulty,
                asset_id=str((generated.secret or {}).get("asset_id") or ""),
            )
        else:
            telemetry.emit(
                phase="challenge", scene=ctx.scene, action=ctx.action, mode=current_risk_mode(), type=generated.type
            )
        return result

    def verify(self, challenge_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """校验入口：同一 challenge 单飞，防止并发请求各自签发 ticket。"""
        with captcha_cache.challenge_guard(challenge_id) as acquired:
            if not acquired:
                raise CaptchaInvalidError()
            return self._verify_unlocked(challenge_id, *args, **kwargs)

    def _verify_unlocked(
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
        sid: str = "",
    ) -> dict[str, Any]:
        started = time.monotonic()
        sig.record_verify(ip)
        if not self._validate_action(scene, action):
            raise CaptchaInvalidError()
        stored = load_stored_challenge(challenge_id, captcha_type, scene, sid, action)
        backend = get_backend(captcha_type)
        if backend is None:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaInvalidError()
        effective_answer = pow_answer if captcha_type == CAPTCHA_TYPE_POW and pow_answer is not None else answer
        attempts = _attempts(challenge_id)
        # 前端以挑战下发的服务端 ts 作为 gen_time：窗口至少要覆盖挑战本身的有效期
        sign_window = max(int(getattr(settings, "CAPTCHA_VERIFY_SIGN_WINDOW", 300) or 300), challenge_ttl() + 60)
        if tickets.verify_sign_enforced() and not tickets.verify_request_signature(
            str(stored.get("ck") or ""),
            challenge_id,
            captcha_type,
            scene,
            effective_answer,
            gen_time,
            nonce,
            sign,
            window=sign_window,
        ):
            raise CaptchaInvalidError()
        difficulty = str(stored.get("difficulty") or "normal")
        passed = answer_passed(backend, stored, effective_answer, captcha_type, difficulty, track, input_mode)
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
            sid=sid,
            ticket_fp=str(stored.get("fp") or ctx.fp),
            ticket_ip=str(stored.get("ip_hash") or ctx.ip_hash),
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
        sig.record_account_fail(account)
        sig.record_hist(ctx.fp, False)
        if attempts >= limit:
            captcha_cache.delete_challenge(challenge_id)
            raise CaptchaInvalidError()
        return {"passed": False}

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
        sid: str = "",
        ticket_fp: str = "",
        ticket_ip: str = "",
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
                raise CaptchaInvalidError()
            ticket = tickets.issue_ticket(
                scene=scene,
                action=action,
                mode=ticket_mode,
                captcha_type=captcha_type,
                ttl=ttl,
                fp=ticket_fp or ctx.fp,
                ip_hash=ticket_ip or ctx.ip_hash,
                sid_hash=sig.hash_value(sid),
            )
        except CaptchaServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 ticket 写入失败 = 本次校验不可用（fail-closed）
            captcha_cache.release_ticket_slot(ip)
            raise CaptchaInvalidError() from exc
        return {"passed": True, "ticket": ticket, "expires_in": ttl}

    def consume_ticket(
        self, ticket: str, scene: str, ip: str = "", action: str = "", fp: str = "", sid: str = ""
    ) -> bool:
        """一次性消费签名 ticket 并校验 (scene, action, sid, fp) 强绑定。"""
        ticket = str(ticket or "").strip()
        if not ticket:
            return False
        try:
            payload = tickets.consume_ticket_payload(
                ticket,
                scene,
                action=action,
                ip_hash=sig.hash_ip(ip),
                fp_hash=sig.hash_value(fp),
                sid_hash=sig.hash_value(sid),
            )
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
            sig.record_ticket_consume(ip)
            captcha_cache.release_ticket_slot(ip)
        return passed

    def audio(self, challenge_id: str, sid: str = "") -> str:
        """读取图片题音频文本；题型 / sid 不符一律按无效挑战处理。"""
        try:
            stored = captcha_cache.load_challenge(challenge_id)
        except Exception as exc:  # noqa: BLE001
            raise CaptchaInvalidError() from exc
        if not stored or stored.get("type") != CAPTCHA_TYPE_IMAGE:
            raise CaptchaInvalidError()
        if strict_binding():
            expected = str(stored.get("sid_hash") or "")
            if not sid or not expected or expected != sig.hash_value(sid):
                raise CaptchaInvalidError()
        text = str((stored.get("secret") or {}).get("audio_text") or "")
        if not text:
            raise CaptchaInvalidError()
        return text


__all__ = [
    "CaptchaInvalidError",
    "CaptchaServiceError",
    "InteractiveCaptcha",
    "build_captcha_meta",
    "enabled_captcha_types",
    "preview_image_captcha",
    "preview_interactive_challenge",
]
