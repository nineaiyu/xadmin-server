#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S5：字段级加密 v3（HKDF + AES-256-GCM）守护。

验证点：
- 写路径统一产出 ``v3:`` 前缀密文（GCM 带认证标签、随机 salt/nonce）；
- 旧 v1（AES-CBC，无前缀）密文仍可解密（存量 api_key/webhook secret 无需迁移）；
- 篡改密文 / 错误密钥在解密时报错，不再静默产出错误明文；
- 数据密钥经 HKDF 与 SECRET_KEY 域隔离（旧 CBC 解析路径读不出 v3 密文）。
"""

import base64

import pytest
from django.conf import settings

from common.base.utils import AESCipher, AESCipherV3, signer

KEY = "unit-test-secret-key"


def test_encrypt_uses_v3_prefix_and_roundtrip():
    cipher = AESCipherV3(KEY)
    token = cipher.encrypt("sk-live-123456")
    assert isinstance(token, bytes)
    assert token.startswith(b"v3:")
    assert cipher.decrypt(token) == "sk-live-123456"
    # 随机 salt/nonce：同一明文两次加密结果不同（避免确定性密文泄漏）
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_decrypt_accepts_legacy_cbc_ciphertext():
    """旧 v1 密文（无前缀 CBC）继续可读：存量数据无需迁移。"""
    legacy_token = AESCipher(KEY).encrypt(b"legacy-secret")
    assert not legacy_token.startswith(b"v3:")
    assert AESCipherV3(KEY).decrypt(legacy_token) == "legacy-secret"


def test_tampered_ciphertext_raises():
    """GCM 认证失败必须报错（旧 CBC 实现无法发现篡改）。"""
    cipher = AESCipherV3(KEY)
    token = cipher.encrypt("payload")
    raw = bytearray(base64.b64decode(token[len(b"v3:") :]))
    raw[-1] ^= 0xFF  # 破坏认证标签
    tampered = b"v3:" + base64.b64encode(bytes(raw))
    with pytest.raises(ValueError):
        cipher.decrypt(tampered)


def test_wrong_key_cannot_decrypt():
    token = AESCipherV3("key-a").encrypt("payload")
    with pytest.raises(ValueError):
        AESCipherV3("key-b").decrypt(token)


def test_v3_ciphertext_not_readable_by_legacy_cipher():
    """HKDF 域隔离：旧 CBC 解析路径（sha256(SECRET_KEY) 派生）解不出 v3 密文。"""
    token = AESCipherV3(KEY).encrypt("payload")
    try:
        out = AESCipher(KEY).decrypt(token)
    except Exception:
        out = None
    assert out != "payload"


def test_module_signer_writes_v3_and_reads_legacy():
    """模块级 signer（api_key/webhook secret 的写读入口）行为守护。"""
    token = signer.encrypt(b"module-level-secret")
    assert token.startswith(b"v3:")
    assert signer.decrypt(token) == "module-level-secret"
    # 同一 SECRET_KEY 产出的旧格式密文仍可读（存量数据兼容）
    legacy = AESCipher(settings.SECRET_KEY).encrypt(b"legacy")
    assert signer.decrypt(legacy) == "legacy"
