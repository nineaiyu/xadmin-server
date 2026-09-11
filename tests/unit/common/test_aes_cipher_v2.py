"""AESCipherV2 双格式（旧 Salted__ / v2 WebCrypto PBKDF2+AES-GCM）单测。

v2 跨端互操作向量由 Node WebCrypto（与浏览器同源实现）生成：
key = "e2e-cross-check-token"，明文 = "CrossCheck@2026 密码"。
"""

import base64
import hashlib

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes

from common.base.utils import AESCipherV2

# Node webcrypto 生成：PBKDF2-HMAC-SHA256 100k / AES-256-GCM / salt[16]+iv[12]+ct+tag[16]
WEBCRYPTO_VECTOR = "RgM6se8zz0QERDwTWB7MNqY7W7oCQaQrAnENQ6EyHhinCeHdP147wLAJGeO4UQTtV5C+Q74OtMq/LghhsoiEvASa"
WEBCRYPTO_KEY = "e2e-cross-check-token"
WEBCRYPTO_PLAINTEXT = "CrossCheck@2026 密码"


def _encrypt_v2(key: str, plain: str, salt: bytes | None = None, iv: bytes | None = None) -> str:
    """按 v2 协议格式构造密文（与服务端 _decrypt_v2 对偶，参数可注入便于篡改用例）。"""
    salt = salt or get_random_bytes(16)
    iv = iv or get_random_bytes(12)
    derived = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, 100_000, dklen=32)
    cipher = AES.new(derived, AES.MODE_GCM, nonce=iv)
    body, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return AESCipherV2.V2_PREFIX + base64.b64encode(salt + iv + body + tag).decode()


class TestLegacySaltedFormat:
    def test_roundtrip(self):
        cipher = AESCipherV2("some-key")
        encrypted = cipher.encrypt("Hello 世界 123".encode()).decode()
        assert cipher.decrypt(encrypted) == "Hello 世界 123"

    def test_non_salted_input_returns_empty(self):
        assert AESCipherV2("some-key").decrypt("bm90LXNhbHRlZA==") == ""

    def test_invalid_base64_returns_empty(self):
        assert AESCipherV2("some-key").decrypt("v2:!!!not-base64!!!") == ""


class TestV2Format:
    def test_cross_compat_with_webcrypto_vector(self):
        """真实 WebCrypto（浏览器同源实现）产出的 v2 密文可被服务端解密。"""
        assert AESCipherV2(WEBCRYPTO_KEY).decrypt(f"v2:{WEBCRYPTO_VECTOR}") == WEBCRYPTO_PLAINTEXT

    def test_roundtrip(self):
        encrypted = _encrypt_v2("some-key", "CrossCheck@2026 密码")
        assert encrypted.startswith("v2:")
        assert AESCipherV2("some-key").decrypt(encrypted) == "CrossCheck@2026 密码"

    def test_bytes_input_with_v2_prefix(self):
        encrypted = _encrypt_v2("some-key", "payload").encode()
        assert AESCipherV2("some-key").decrypt(encrypted) == "payload"

    def test_wrong_key_returns_empty(self):
        encrypted = _encrypt_v2("key-a", "payload")
        assert AESCipherV2("key-b").decrypt(encrypted) == ""

    def test_tampered_ciphertext_returns_empty(self):
        encrypted = _encrypt_v2("some-key", "payload")
        raw = bytearray(base64.b64decode(encrypted[len(AESCipherV2.V2_PREFIX) :]))
        raw[-1] ^= 0x01  # 破坏 GCM tag
        tampered = AESCipherV2.V2_PREFIX + base64.b64encode(bytes(raw)).decode()
        assert AESCipherV2("some-key").decrypt(tampered) == ""

    def test_truncated_body_returns_empty(self):
        encrypted = _encrypt_v2("some-key", "payload")
        raw = base64.b64decode(encrypted[len(AESCipherV2.V2_PREFIX) :])[:20]
        truncated = AESCipherV2.V2_PREFIX + base64.b64encode(raw).decode()
        assert AESCipherV2("some-key").decrypt(truncated) == ""

    def test_random_iv_produces_unique_ciphertext(self):
        assert _encrypt_v2("key", "same") != _encrypt_v2("key", "same")


@pytest.mark.parametrize(
    "key,plain",
    [
        ("verify-token-uuid-like", "P@ssw0rd!2026"),
        ("admin", "管理员密码 ABC123"),
    ],
)
def test_both_formats_share_decrypt_entry(key, plain):
    """同一密钥下旧格式与 v2 格式均能通过 decrypt 入口解出原文。"""
    cipher = AESCipherV2(key)
    legacy = cipher.encrypt(plain.encode()).decode()
    v2 = _encrypt_v2(key, plain)
    assert cipher.decrypt(legacy) == plain
    assert cipher.decrypt(v2) == plain
