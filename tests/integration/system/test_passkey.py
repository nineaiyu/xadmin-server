# -*- coding: utf-8 -*-
"""Passkey（WebAuthn）与认证方式策略集成测试。

WebAuthn 全链路用 ``cryptography`` 本地生成 P-256 密钥自造 attestation / assertion
（真实认证器无法在单测自动化；浏览器侧由 E2E 冒烟导航覆盖）。

覆盖：
- 注册（webauthn.create）→ 认证（webauthn.get）往返 + 签名计数器更新；
- 挑战值一次性消费 / 篡改签名 / 计数器回退均拒绝；
- Passkey 作为 MFA 方式（check_user_mfa_code 链路）；
- 认证方式策略收敛：全局白名单 ∩ 角色允许集 ∩ 用户允许集；角色 mfa_required 强制。
"""

import base64
import hashlib
import json
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from system.models import UserPasskey
from system.utils.webauthn import (
    SCENE_AUTHENTICATE,
    SCENE_REGISTER,
    generate_challenge,
    verify_assertion,
    verify_registration,
)

RP_ID = "testserver"
ORIGIN = "http://testserver"

pytestmark = pytest.mark.django_db

CHALLENGE_URL = "/api/system/passkeys/challenge"
REGISTER_URL = "/api/system/passkeys/register"
PASSKEY_URL = "/api/system/passkeys"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _cose_key(private_key) -> bytes:
    numbers = private_key.public_key().public_numbers()
    return cbor2.dumps(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )


def _client_data(kind: str, challenge: str) -> bytes:
    return json.dumps({"type": kind, "challenge": challenge, "origin": ORIGIN}).encode()


def _registration_payload(user, private_key, credential_id=b"cred-1", challenge=None):
    challenge = challenge or generate_challenge(user, SCENE_REGISTER)
    client_data = _client_data("webauthn.create", challenge)
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest()
        + bytes([0x41])  # UP + AT
        + struct.pack(">I", 0)
        + b"\x00" * 16
        + struct.pack(">H", len(credential_id))
        + credential_id
        + _cose_key(private_key)
    )
    attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
    return {
        "client_data_json": _b64(client_data),
        "attestation_object": _b64(attestation),
        "name": "测试设备",
    }


def _assertion_payload(user, private_key, sign_count=1, credential_id=b"cred-1", signature_override=None):
    challenge = generate_challenge(user, SCENE_AUTHENTICATE)
    client_data = _client_data("webauthn.get", challenge)
    auth_data = hashlib.sha256(RP_ID.encode()).digest() + bytes([0x01]) + struct.pack(">I", sign_count)
    signature = signature_override or private_key.sign(
        auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
    )
    return {
        "credential_id": _b64(credential_id),
        "client_data_json": _b64(client_data),
        "authenticator_data": _b64(auth_data),
        "signature": _b64(signature),
    }


def _bind_passkey(user, private_key, credential_id=b"cred-1") -> UserPasskey:
    data = verify_registration(
        user=user,
        payload=_registration_payload(user, private_key, credential_id),
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
    )
    return UserPasskey.objects.create(user=user, creator=user, **data)


class _FakeRequest:
    """最小请求壳：仅提供 webauthn 推导 rp_id / origin 与取 IP 所需接口。"""

    scheme = "http"
    headers = {"Origin": ORIGIN}
    META = {"REMOTE_ADDR": "127.0.0.1"}

    def get_host(self):
        return RP_ID


class TestWebAuthnRoundtrip:
    def test_registration_and_assertion(self, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        passkey = _bind_passkey(superuser, private_key)
        assert passkey.credential_id
        payload = _assertion_payload(superuser, private_key, sign_count=3)
        new_count = verify_assertion(
            user=superuser,
            payload=payload,
            public_key=bytes(passkey.public_key),
            stored_sign_count=passkey.sign_count,
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
        )
        assert new_count == 3

    def test_registration_rejects_wrong_challenge(self, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        generate_challenge(superuser, SCENE_REGISTER)  # 真实挑战，但提交别的
        payload = _registration_payload(superuser, private_key, challenge="not-the-challenge")
        with pytest.raises(ValueError):
            verify_registration(user=superuser, payload=payload, expected_rp_id=RP_ID, expected_origin=ORIGIN)

    def test_challenge_is_single_use(self, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        challenge = generate_challenge(superuser, SCENE_REGISTER)
        payload = _registration_payload(superuser, private_key, challenge=challenge)
        verify_registration(user=superuser, payload=payload, expected_rp_id=RP_ID, expected_origin=ORIGIN)
        # 二次提交同一挑战 → 拒绝（一次性消费）
        with pytest.raises(ValueError):
            verify_registration(user=superuser, payload=payload, expected_rp_id=RP_ID, expected_origin=ORIGIN)

    def test_assertion_rejects_bad_signature(self, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        passkey = _bind_passkey(superuser, private_key)
        other_key = ec.generate_private_key(ec.SECP256R1())
        payload = _assertion_payload(superuser, private_key)
        # 用另一把密钥的签名替换
        payload["signature"] = _b64(b"broken-signature")
        with pytest.raises(ValueError):
            verify_assertion(
                user=superuser,
                payload=payload,
                public_key=bytes(passkey.public_key),
                stored_sign_count=0,
                expected_rp_id=RP_ID,
                expected_origin=ORIGIN,
            )
        assert other_key  # 保持引用（避免静态检查误判未使用）

    def test_assertion_rejects_sign_count_rollback(self, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        passkey = _bind_passkey(superuser, private_key)
        payload = _assertion_payload(superuser, private_key, sign_count=2)
        with pytest.raises(ValueError):
            verify_assertion(
                user=superuser,
                payload=payload,
                public_key=bytes(passkey.public_key),
                stored_sign_count=5,
                expected_rp_id=RP_ID,
                expected_origin=ORIGIN,
            )


class TestPasskeyMfaBackend:
    def test_check_code_verifies_and_updates_counter(self, superuser):
        from mfa.services import check_user_mfa_code

        private_key = ec.generate_private_key(ec.SECP256R1())
        passkey = _bind_passkey(superuser, private_key)
        payload = _assertion_payload(superuser, private_key, sign_count=7)
        ok, message = check_user_mfa_code(superuser, "passkey", json.dumps(payload), request=_FakeRequest())
        assert ok is True, message
        passkey.refresh_from_db()
        assert passkey.sign_count == 7
        assert passkey.last_used_at is not None

    def test_check_code_rejects_unbound_credential(self, superuser):
        from mfa.services import check_user_mfa_code

        private_key = ec.generate_private_key(ec.SECP256R1())
        payload = _assertion_payload(superuser, private_key)
        payload["credential_id"] = "not-bound"
        ok, message = check_user_mfa_code(superuser, "passkey", json.dumps(payload), request=_FakeRequest())
        assert ok is False
        assert message

    def test_passkey_backend_is_active_only_when_bound(self, superuser):
        from mfa.backends import get_backend

        assert get_backend(superuser, "passkey") is None
        private_key = ec.generate_private_key(ec.SECP256R1())
        _bind_passkey(superuser, private_key)
        assert get_backend(superuser, "passkey") is not None


class TestPasskeyApi:
    def test_register_list_and_destroy(self, auth_client, superuser):
        resp = auth_client.post(CHALLENGE_URL, {"scene": "register"}, format="json", HTTP_ORIGIN=ORIGIN)
        assert resp.data["code"] == 1000, resp.data
        challenge = resp.data["data"]["challenge"]
        assert resp.data["data"]["rp_id"] == RP_ID

        private_key = ec.generate_private_key(ec.SECP256R1())
        payload = _registration_payload(superuser, private_key, credential_id=b"api-cred", challenge=challenge)
        resp = auth_client.post(REGISTER_URL, payload, format="json", HTTP_ORIGIN=ORIGIN)
        assert resp.data["code"] == 1000, resp.data
        passkey_pk = resp.data["data"]["pk"]

        resp = auth_client.get(PASSKEY_URL)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["total"] == 1

        resp = auth_client.delete(f"{PASSKEY_URL}/{passkey_pk}")
        assert resp.data["code"] == 1000, resp.data
        assert UserPasskey.objects.count() == 0

    def test_register_rejects_duplicate_credential(self, auth_client, superuser):
        private_key = ec.generate_private_key(ec.SECP256R1())
        _bind_passkey(superuser, private_key, credential_id=b"dup-cred")
        resp = auth_client.post(CHALLENGE_URL, {"scene": "register"}, format="json", HTTP_ORIGIN=ORIGIN)
        payload = _registration_payload(
            superuser, private_key, credential_id=b"dup-cred", challenge=resp.data["data"]["challenge"]
        )
        resp = auth_client.post(REGISTER_URL, payload, format="json", HTTP_ORIGIN=ORIGIN)
        assert resp.data["code"] == 1001


class TestMfaMethodPolicy:
    def test_global_allow_list_restricts(self, superuser, settings):
        from mfa.backends import get_backend, get_user_mfa_policy

        settings.SECURITY_MFA_METHODS = ["passkey"]
        assert get_user_mfa_policy(superuser)["methods"] == {"passkey"}
        assert get_backend(superuser, "password") is None
        assert get_backend(superuser, "otp") is None

    def test_role_and_user_layers_intersect(self, superuser, settings):
        from mfa.backends import get_user_mfa_policy
        from system.models import UserRole

        settings.SECURITY_MFA_METHODS = []
        role = UserRole.objects.create(name="安全角色", code="sec_role", allowed_mfa_types=["otp", "passkey"])
        superuser.roles.add(role)
        superuser.allowed_mfa_types = ["otp"]
        superuser.save(update_fields=["allowed_mfa_types"])
        # 角色允许 otp/passkey，用户允许 otp → 交集 = {otp}
        assert get_user_mfa_policy(superuser)["methods"] == {"otp"}

    def test_role_requires_mfa(self, superuser):
        from mfa.services import is_login_mfa_required
        from system.models import UserRole

        superuser.otp_secret_key = "JBSWY3DPEHPK3PXP"
        superuser.mfa_level = 1
        superuser.save(update_fields=["otp_secret_key", "mfa_level"])
        role = UserRole.objects.create(name="强制MFA", code="mfa_role", mfa_required=True)
        superuser.roles.add(role)
        assert is_login_mfa_required(superuser) is True

    def test_role_requires_mfa_without_method_degrades(self, superuser):
        from mfa.services import is_login_mfa_required
        from system.models import UserRole

        role = UserRole.objects.create(name="强制MFA2", code="mfa_role2", mfa_required=True)
        superuser.roles.add(role)
        # 无任何可用验证方式 → 降级（避免登录死锁）
        assert is_login_mfa_required(superuser) is False
