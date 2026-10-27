#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""结构化签名票据与 verify 请求加签（票据层）。

票据从「不记名代金券」升级为自证式签名票据：

- ``ticket = b64url(payload) + "." + hex(hmac_sha256(key, payload_b64))``；
- payload 携带 ``v/tid/kid/scene/action/mode/type/risk/fp/ip/iat/exp/n``；
- Redis 只存 ``captcha_nonce_{n} -> exp``（一次性消费），票据内容不进缓存；
- 消费校验顺序：验签 -> ``exp`` -> ``atomic_get_delete(nonce)`` -> ``scene``
  -> ``action`` -> ``fp``/``ip`` 弱绑定（弱绑定：不一致按需重验，不硬拒）。

密钥：``CAPTCHA_TICKET_SECRET`` 为唯一定义处（config.yml）；未配置时由
``SECRET_KEY`` 派生并告警一次；``CAPTCHA_TICKET_SECRET_PREVIOUS``
（``key_id:secret``）与当前密钥并行验签，实现轮换不断流。

verify 请求自身加签（协议轴，防离线重放一次 verify 调用）：
``sign = hmac_sha256(ck, "{challenge_id}|{type}|{scene}|{answer_digest}|{gen_time}")``；
``ck`` 由 challenge 下发并随挑战缓存同寿，``captcha_seen_{nonce}`` 挡重放。
默认 ``CAPTCHA_RISK_MODE=off`` 时不强制（升级零行为变化）。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from django.conf import settings

from captcha.const import (
    CAPTCHA_RISK_MODE_OFF,
    CAPTCHA_SCENE_ACTIONS,
    current_risk_mode,
)
from common.cache.storage import atomic_get_delete
from common.utils import get_logger

logger = get_logger(__name__)

TICKET_VERSION = 1
NONCE_KEY_PREFIX = "captcha_nonce"
SEEN_NONCE_KEY_PREFIX = "captcha_seen"
#: verify 签名 nonce 的防重放窗口（与时间窗一致）
SEEN_NONCE_TTL = 300
#: 派生密钥用途域（换域即全量作废存量票据）
_DERIVED_SECRET_INFO = b"xadmin:captcha-ticket:v1"
_secret_warned = False


def _derived_secret() -> str:
    """由 SECRET_KEY 派生票据密钥（未显式配置时的兜底，只告警一次）。"""
    root = str(getattr(settings, "SECRET_KEY", "") or "")
    return hmac.new(root.encode(), _DERIVED_SECRET_INFO, hashlib.sha256).hexdigest()


def ticket_keys() -> dict[str, str]:
    """当前可用验签密钥表：``key_id -> secret``（含轮换期旧密钥）。"""
    keys: dict[str, str] = {}
    secret = str(getattr(settings, "CAPTCHA_TICKET_SECRET", "") or "").strip()
    key_id = str(getattr(settings, "CAPTCHA_TICKET_SECRET_KEY_ID", "") or "k1").strip() or "k1"
    if not secret:
        global _secret_warned
        if not _secret_warned:
            logger.warning("CAPTCHA_TICKET_SECRET unset; deriving from SECRET_KEY (set it in config.yml to pin)")
            _secret_warned = True
        key_id = "derived"
        secret = _derived_secret()
    keys[key_id] = secret
    previous = str(getattr(settings, "CAPTCHA_TICKET_SECRET_PREVIOUS", "") or "").strip()
    if previous and ":" in previous:
        prev_id, prev_secret = previous.split(":", 1)
        if prev_id.strip() and prev_secret.strip():
            keys[prev_id.strip()] = prev_secret.strip()
    return keys


def ticket_ttl_for(mode: str) -> int:
    """按风险档位取票据时效：challenge 档沿用 CAPTCHA_TICKET_TTL。"""
    mapping = {
        "allow": ("CAPTCHA_TICKET_TTL_ALLOW", 60),
        "light": ("CAPTCHA_TICKET_TTL_LIGHT", 90),
        "hard": ("CAPTCHA_TICKET_TTL_HARD", 90),
    }
    key, default = mapping.get(mode, ("CAPTCHA_TICKET_TTL", 120))
    return max(int(getattr(settings, key, default) or default), 10)


def _nonce_key(nonce: str) -> str:
    return f"{NONCE_KEY_PREFIX}_{nonce}"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def action_allowed_for_scene(scene: str, action: str) -> bool:
    """票据 / verify 声明的 action 必须落在该 scene 的服务端登记表内。"""
    return action in CAPTCHA_SCENE_ACTIONS.get(scene, ())


def action_binding_enabled() -> bool:
    """票据动作绑定开关：关掉即回退到只绑 scene 的旧语义。"""
    return bool(getattr(settings, "CAPTCHA_ACTION_BIND", True))


def issue_ticket(
    scene: str,
    action: str = "",
    mode: str = "challenge",
    captcha_type: str = "",
    ttl: int | None = None,
    fp: str = "",
    ip_hash: str = "",
) -> str:
    """签发结构化票据并在 Redis 登记一次性 nonce；缓存写入失败由调用方 fail-closed。"""
    from django.core.cache import cache

    if ttl is None:
        ttl = ticket_ttl_for(mode)
    now = int(time.time())
    payload = {
        "v": TICKET_VERSION,
        "tid": uuid.uuid4().hex,
        "kid": next(iter(ticket_keys())),
        "scene": scene,
        "action": action or "",
        "mode": mode,
        "type": captcha_type or "",
        "fp": fp or "",
        "ip": ip_hash or "",
        "iat": now,
        "exp": now + int(ttl),
        "n": secrets.token_hex(16),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    ticket = f"{body}.{_sign(ticket_keys()[str(payload['kid'])], body)}"
    # nonce 一次性登记：TTL 在票据时效上留 60s 余量（覆盖慢速消费与时钟漂移）
    cache.set(_nonce_key(str(payload["n"])), payload["exp"], int(ttl) + 60)
    return ticket


def parse_ticket(ticket: str) -> dict[str, Any] | None:
    """拆解并验签票据；格式 / 签名不符返回 None（不抛异常，防探测同形）。"""
    ticket = str(ticket or "").strip()
    if ticket.count(".") != 1:
        return None
    body, _, signature = ticket.partition(".")
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != TICKET_VERSION:
        return None
    kid = str(payload.get("kid") or "")
    secret = ticket_keys().get(kid)
    if not secret or not hmac.compare_digest(_sign(secret, body), signature):
        return None
    return payload


def consume_ticket_payload(
    ticket: str, scene: str, action: str = "", ip_hash: str = "", fp_hash: str = ""
) -> dict[str, Any] | None:
    """一次性消费票据并校验 (scene, action) 绑定；成功返回票据载荷（供留痕）。"""

    payload = parse_ticket(ticket)
    if payload is None:
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        return None
    # 一次性：并发只有一方能 atomic 取回 nonce（取不回 = 已消费 / 从未签发）
    try:
        value = atomic_get_delete(_nonce_key(str(payload.get("n") or "")))
    except Exception:  # noqa: BLE001 缓存不可用 = 拒绝（fail-closed）
        return None
    if value is None:
        return None
    if payload.get("scene") != scene:
        return None
    if not action_binding_enabled():
        return payload
    # 强绑定：签发与消费两端必须声明且完全一致。未绑 action 的票据只可能
    # 来自绑定关闭窗口（或旧格式遗留），一律拒绝并要求重做验证——否则
    # 「verify 省略 action」即可签发万能票，跨动作挪用防不住。
    bound = str(payload.get("action") or "")
    if not bound or not action or bound != action:
        return None
    # IP / 指纹弱绑定：不一致时要求重新验证（不硬拒其它票据）
    if _weak_binding_drifted(payload, ip_hash, fp_hash):
        return None
    return payload


def consume_ticket(ticket: str, scene: str, action: str = "", ip_hash: str = "", fp_hash: str = "") -> bool:
    """一次性消费票据并校验 (scene, action) 绑定；网段漂移按需重验。"""
    return consume_ticket_payload(ticket, scene, action=action, ip_hash=ip_hash, fp_hash=fp_hash) is not None


def _weak_binding_drifted(payload: dict[str, Any], ip_hash: str = "", fp_hash: str = "") -> bool:
    """IP / 指纹弱绑定漂移：两端都有值且不同 → 需重新验证（不硬拒其它票据）。"""
    bound_ip = str(payload.get("ip") or "")
    if bound_ip and ip_hash and bound_ip != ip_hash:
        return True
    bound_fp = str(payload.get("fp") or "")
    return bool(bound_fp and fp_hash and bound_fp != fp_hash)


# ---------------------------------------------------------------------------
# verify 请求加签：challenge 下发一次性会话密钥 ck，verify 携带签名与时间窗
# ---------------------------------------------------------------------------


def verify_sign_enforced() -> bool:
    """verify 签名只在风险层非 off 档时强制（off = 升级零行为变化）。"""
    required = bool(getattr(settings, "CAPTCHA_VERIFY_SIGN_REQUIRED", True))
    return required and current_risk_mode() != CAPTCHA_RISK_MODE_OFF


def new_session_key() -> str:
    """challenge 会话密钥：仅本题有效，随挑战缓存同寿，不落日志。"""
    return secrets.token_urlsafe(32)


def answer_digest(answer: Any) -> str:
    """答案摘要：紧凑 JSON 的 sha256（与前端 JSON.stringify 同口径，键序保持提交序）。"""
    raw = json.dumps(answer, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def compute_verify_sign(ck: str, challenge_id: str, captcha_type: str, scene: str, digest: str, gen_time: int) -> str:
    """verify 请求签名：hmac_sha256(ck, canonical(challenge_id, type, scene, digest, gen_time))。"""
    canonical = "|".join([challenge_id, captcha_type, scene, digest, str(int(gen_time))])
    return hmac.new(ck.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify_request_signature(
    ck: str,
    challenge_id: str,
    captcha_type: str,
    scene: str,
    answer: Any,
    gen_time: Any,
    nonce: Any,
    sign: Any,
) -> bool:
    """校验 verify 请求签名、时间窗与 nonce 未见过；任一不符即失败。"""
    try:
        gen_time = int(gen_time)
    except (TypeError, ValueError):
        return False
    window = max(int(getattr(settings, "CAPTCHA_VERIFY_SIGN_WINDOW", 300) or 300), 30)
    if abs(int(time.time()) - gen_time) > window:
        return False
    nonce = str(nonce or "").strip()
    if len(nonce) < 16 or len(nonce) > 128:
        return False
    if not sign or not str(sign).strip():
        return False
    expected = compute_verify_sign(ck, challenge_id, captcha_type, scene, answer_digest(answer), gen_time)
    if not hmac.compare_digest(expected, str(sign).strip()):
        return False
    # nonce 防重放：add 失败 = 已见过（同一签名包重放必被拒）
    from django.core.cache import cache

    try:
        return bool(cache.add(f"{SEEN_NONCE_KEY_PREFIX}_{nonce}", 1, SEEN_NONCE_TTL))
    except Exception:  # noqa: BLE001 缓存不可用时验签侧按失败处理（协议面 fail-closed）
        return False
