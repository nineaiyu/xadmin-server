#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""WebAuthn / Passkey 服务端校验（F-9）。

不引入第三方 WebAuthn 依赖：CBOR 解析用 ``cbor2``（kombu 既有依赖，已在
requirements 显式声明），密码学运算用 ``cryptography``（既有依赖）。

覆盖范围（供 Passkey 登录 / 敏感操作二次确认两条链路共用）：

- 注册（``webauthn.create``）：校验 clientDataJSON（type / challenge / origin）、
  attestationObject 的 authData（rpIdHash / UP / AT 标志）、提取 credentialId 与 COSE 公钥；
- 认证（``webauthn.get``）：校验同一组 clientData 字段 + authenticatorData 的
  rpIdHash / UP 标志 + 签名（ES256 / RS256 / Ed25519）+ 签名计数器单调性。

挑战值（challenge）服务端生成并缓存（一次性消费，TTL 300s），防重放。
"""

import base64
import hashlib
import os
import secrets
import struct

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

CHALLENGE_TTL = 300
# 场景：注册新凭据 / 认证（登录 MFA 与 412 二次确认共用认证场景）
SCENE_REGISTER = "register"
SCENE_AUTHENTICATE = "authenticate"

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40
FLAG_BACKUP_ELIGIBLE = 0x08
FLAG_BACKED_UP = 0x10

CHALLENGE_CACHE_KEY = "passkey_challenge_{scene}_{user_pk}"


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(value: str) -> bytes:
    text = str(value or "")
    padding_needed = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + "=" * padding_needed)


def generate_challenge(user, scene=SCENE_AUTHENTICATE) -> str:
    """生成并缓存一次性挑战值（base64url）。"""
    challenge = b64url_encode(os.urandom(32))
    cache.set(CHALLENGE_CACHE_KEY.format(scene=scene, user_pk=user.pk), challenge, CHALLENGE_TTL)
    return challenge


def consume_challenge(user, scene, challenge) -> bool:
    """校验并消费挑战值（一次性；不匹配或已过期返回 False）。"""
    key = CHALLENGE_CACHE_KEY.format(scene=scene, user_pk=user.pk)
    saved = cache.get(key)
    if not saved or not challenge or not secrets.compare_digest(str(saved), str(challenge)):
        return False
    cache.delete(key)
    return True


def rp_id_and_origin(request) -> tuple:
    """从请求推导 WebAuthn 的 RP ID 与 origin（RP ID = 主机名不含端口）。"""
    host = request.get_host() if request is not None else ""
    rp_id = host.split(":")[0]
    origin = ""
    if request is not None:
        origin = request.headers.get("Origin") or f"{request.scheme}://{host}"
    return rp_id, origin


def parse_client_data(client_data_json: bytes, expected_type: str, expected_origin: str = "") -> dict:
    """解析并校验 clientDataJSON，返回 {"type", "challenge", "origin"}；非法抛 ValueError。"""
    import json

    try:
        data = json.loads(client_data_json.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 客户端数据不可信，统一归一为校验失败
        raise ValueError(str(_("Invalid client data"))) from exc
    if data.get("type") != expected_type:
        raise ValueError(str(_("Invalid client data type")))
    if expected_origin and data.get("origin") != expected_origin:
        raise ValueError(str(_("Invalid origin")))
    if not data.get("challenge"):
        raise ValueError(str(_("Missing challenge")))
    return data


def parse_authenticator_data(auth_data: bytes, require_attested: bool = False) -> dict:
    """解析 authenticatorData（注册需带 attestedCredentialData）。"""
    if len(auth_data) < 37:
        raise ValueError(str(_("Invalid authenticator data")))
    rp_id_hash = auth_data[:32]
    flags = auth_data[32]
    sign_count = struct.unpack(">I", auth_data[33:37])[0]
    result = {
        "rp_id_hash": rp_id_hash,
        "flags": flags,
        "sign_count": sign_count,
        "user_present": bool(flags & FLAG_USER_PRESENT),
        "user_verified": bool(flags & FLAG_USER_VERIFIED),
        "backed_up": bool(flags & FLAG_BACKED_UP),
    }
    if flags & FLAG_ATTESTED_CREDENTIAL_DATA:
        if len(auth_data) < 55:
            raise ValueError(str(_("Invalid attested credential data")))
        aaguid = auth_data[37:53]
        cred_len = struct.unpack(">H", auth_data[53:55])[0]
        credential_id = auth_data[55 : 55 + cred_len]
        cose_key = auth_data[55 + cred_len :]
        if len(credential_id) != cred_len or not cose_key:
            raise ValueError(str(_("Invalid attested credential data")))
        result.update(
            {
                "aaguid": str(aaguid.hex()),
                "credential_id": credential_id,
                "cose_key": cose_key,
            }
        )
    elif require_attested:
        raise ValueError(str(_("Missing attested credential data")))
    return result


def _public_key_from_cose(cose_key: bytes):
    """COSE 公钥 → (cryptography 公钥对象, COSE alg)。支持 ES256 / RS256 / Ed25519。"""
    cose = cbor2.loads(bytes(cose_key))
    kty = cose.get(1)
    alg = cose.get(3)
    if kty == 2:  # EC2
        if cose.get(-1) != 1:  # crv: 1 = P-256
            raise ValueError(str(_("Unsupported elliptic curve")))
        numbers = ec.EllipticCurvePublicNumbers(
            int.from_bytes(cose.get(-2), "big"), int.from_bytes(cose.get(-3), "big"), ec.SECP256R1()
        )
        return numbers.public_key(), alg or -7
    if kty == 3:  # RSA
        numbers = rsa.RSAPublicNumbers(int.from_bytes(cose.get(-2), "big"), int.from_bytes(cose.get(-1), "big"))
        return numbers.public_key(), alg or -257
    if kty == 1:  # OKP
        if cose.get(-1) != 6:  # crv: 6 = Ed25519
            raise ValueError(str(_("Unsupported elliptic curve")))
        return ed25519.Ed25519PublicKey.from_public_bytes(cose.get(-2)), alg or -8
    raise ValueError(str(_("Unsupported public key type")))


def verify_cose_signature(cose_key: bytes, signature: bytes, signed_data: bytes) -> None:
    """用 COSE 公钥验签；失败抛 InvalidSignature。"""
    public_key, alg = _public_key_from_cose(cose_key)
    if alg == -7:
        public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
    elif alg == -257:
        public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
    elif alg == -8:
        public_key.verify(signature, signed_data)
    else:
        raise ValueError(str(_("Unsupported signature algorithm")))


def verify_registration(*, user, payload: dict, expected_rp_id: str, expected_origin: str) -> dict:
    """校验注册响应，返回 {"credential_id", "public_key", "sign_count", "aaguid", "backed_up", "name"}。

    payload 形态（前端 base64url 编码）：{"client_data_json", "attestation_object", "name"?}
    """
    client_data = parse_client_data(
        b64url_decode(payload.get("client_data_json", "")), "webauthn.create", expected_origin
    )
    if not consume_challenge(user, SCENE_REGISTER, client_data["challenge"]):
        raise ValueError(str(_("Challenge verification failed, please retry")))
    try:
        attestation = cbor2.loads(b64url_decode(payload.get("attestation_object", "")))
        auth_data = attestation["authData"]
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(_("Invalid attestation object"))) from exc
    parsed = parse_authenticator_data(auth_data, require_attested=True)
    if parsed["rp_id_hash"] != hashlib.sha256(expected_rp_id.encode()).digest():
        raise ValueError(str(_("Invalid relying party id")))
    if not parsed["user_present"]:
        raise ValueError(str(_("User presence is required")))
    return {
        "credential_id": b64url_encode(parsed["credential_id"]),
        "public_key": bytes(parsed["cose_key"]),
        "sign_count": parsed["sign_count"],
        "aaguid": parsed["aaguid"],
        "backed_up": parsed.get("backed_up", False),
        "name": str(payload.get("name") or "")[:64],
    }


def verify_assertion(
    *, user, payload: dict, public_key, stored_sign_count: int, expected_rp_id: str, expected_origin: str
) -> int:
    """校验认证响应，返回新的签名计数器；失败抛 ValueError。"""
    client_data_bytes = b64url_decode(payload.get("client_data_json", ""))
    client_data = parse_client_data(client_data_bytes, "webauthn.get", expected_origin)
    if not consume_challenge(user, SCENE_AUTHENTICATE, client_data["challenge"]):
        raise ValueError(str(_("Challenge verification failed, please retry")))
    try:
        auth_data = b64url_decode(payload.get("authenticator_data", ""))
        signature = b64url_decode(payload.get("signature", ""))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(_("Invalid signature data"))) from exc
    parsed = parse_authenticator_data(auth_data)
    if parsed["rp_id_hash"] != hashlib.sha256(expected_rp_id.encode()).digest():
        raise ValueError(str(_("Invalid relying party id")))
    if not parsed["user_present"]:
        raise ValueError(str(_("User presence is required")))
    # 签名计数器单调性：两端都非 0 且新值不大于旧值 = 疑似凭据克隆，拒绝
    if parsed["sign_count"] and stored_sign_count and parsed["sign_count"] <= stored_sign_count:
        raise ValueError(str(_("Signature counter check failed, the credential may be cloned")))
    signed_data = auth_data + hashlib.sha256(client_data_bytes).digest()
    try:
        verify_cose_signature(bytes(public_key), signature, signed_data)
    except InvalidSignature as exc:
        raise ValueError(str(_("Signature verification failed"))) from exc
    return parsed["sign_count"]
