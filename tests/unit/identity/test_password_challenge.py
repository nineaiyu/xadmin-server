# -*- coding: utf-8 -*-
"""改密口令加密挑战（v4 协议）：随机密钥下发 / 轮换 / 存量客户端兼容解密。"""

import base64
import hashlib

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes
from rest_framework.exceptions import ValidationError

from common.base.crypto import AESCipherV2
from identity.models import UserInfo
from identity.serializers.userinfo import (
    issue_password_challenge,
    load_password_challenge,
)

pytestmark = pytest.mark.django_db

CHALLENGE_URL = "/api/identity/userinfo/password-challenge"
CONFIRM_URL = "/api/mfa/confirm"


def _encrypt_v4(challenge: str, plain: str) -> str:
    """按 v4 协议构造密文（与前端 AesEncryptedChallenge 同构：v2 payload 换 v4 前缀）。"""
    salt = get_random_bytes(16)
    iv = get_random_bytes(12)
    derived = hashlib.pbkdf2_hmac("sha256", challenge.encode(), salt, 100_000, dklen=32)
    cipher = AES.new(derived, AES.MODE_GCM, nonce=iv)
    body, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return AESCipherV2.V4_PREFIX + base64.b64encode(salt + iv + body + tag).decode()


class TestChallengeIssueAndLoad:
    def test_issue_returns_random_value_and_rotates(self):
        user = UserInfo.objects.create_user(username="pwd-chg-user", password="Test@123456")
        first = issue_password_challenge(user)
        second = issue_password_challenge(user)
        assert first and second and first != second
        assert load_password_challenge(user) == second

    def test_load_missing_challenge_returns_empty(self):
        user = UserInfo.objects.create_user(username="pwd-chg-nouser", password="Test@123456")
        assert load_password_challenge(user) == ""

    def test_challenges_are_isolated_per_user(self):
        user_a = UserInfo.objects.create_user(username="pwd-chg-a", password="Test@123456")
        user_b = UserInfo.objects.create_user(username="pwd-chg-b", password="Test@123456")
        issue_password_challenge(user_a)
        assert load_password_challenge(user_b) == ""


class TestDecryptSubmittedPassword:
    @staticmethod
    def _user():
        return UserInfo.objects.create_user(username="pwd-decrypt-user", password="Test@123456")

    def test_v4_challenge_ciphertext_decrypts(self, settings):
        from identity.serializers.userinfo import decrypt_submitted_password

        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
        user = self._user()
        challenge = issue_password_challenge(user)
        plain = decrypt_submitted_password(user, _encrypt_v4(challenge, "NewP@ss2026"))
        assert plain == "NewP@ss2026"

    def test_v4_without_challenge_rejected_no_fallback(self, settings):
        """挑战缺失/过期按解密失败拒绝，不回退 username 密钥（防降级）。"""
        from identity.serializers.userinfo import decrypt_submitted_password

        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
        user = self._user()
        # 未领取挑战：即便用 username 密钥构造的 v4 密文也必须被拒绝
        forged = _encrypt_v4(user.username, "forged")
        with pytest.raises(ValidationError):
            decrypt_submitted_password(user, forged)

    def test_v4_wrong_challenge_rejected(self, settings):
        from identity.serializers.userinfo import decrypt_submitted_password

        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
        user = self._user()
        issue_password_challenge(user)  # 当前挑战与密文所用密钥不同
        with pytest.raises(ValidationError):
            decrypt_submitted_password(user, _encrypt_v4("other-challenge", "value"))

    def test_username_protocol_still_accepted(self, settings):
        """存量客户端兼容：v1/v2（username 密钥）密文继续可解。"""
        from identity.serializers.userinfo import decrypt_submitted_password

        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
        user = self._user()
        legacy = AESCipherV2(user.username).encrypt(b"LegacyPass@1").decode()
        assert decrypt_submitted_password(user, legacy) == "LegacyPass@1"

    def test_plaintext_fallback_when_switch_off(self, settings):
        from identity.serializers.userinfo import decrypt_submitted_password

        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False
        user = self._user()
        # 关闭开关：非法密文按提交明文核验（导入 / E2E 场景）
        assert decrypt_submitted_password(user, "PlainSubmit@1") == "PlainSubmit@1"


class TestChallengeEndpoint:
    def test_endpoint_requires_authentication(self, client):
        response = client.get(CHALLENGE_URL)
        assert response.status_code in (401, 403)

    def test_endpoint_issues_and_rotates(self, api_client, superuser):
        api_client.force_authenticate(user=superuser)
        first = api_client.get(CHALLENGE_URL).json()
        assert first["code"] == 1000
        assert first["data"]["challenge"] and first["data"]["expires_in"] > 0
        second = api_client.get(CHALLENGE_URL).json()
        assert second["data"]["challenge"] != first["data"]["challenge"]

    def test_v4_change_password_full_chain(self, api_client, superuser, settings):
        """端到端：密码二次确认 → 领挑战 → v4 密文改密 → 成功。"""
        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
        api_client.force_authenticate(user=superuser)
        api_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"})
        challenge = api_client.get(CHALLENGE_URL).json()["data"]["challenge"]
        payload = {
            "old_password": _encrypt_v4(challenge, "Admin@123456"),
            "sure_password": _encrypt_v4(challenge, "FreshPass@2026"),
        }
        response = api_client.post("/api/identity/userinfo/reset-password", payload, format="json")
        assert response.status_code == 200, response.content
        assert superuser.check_password("FreshPass@2026")
