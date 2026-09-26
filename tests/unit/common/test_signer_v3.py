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


def test_master_key_rotation_reads_old_ciphertext():
    """主密钥轮换（3.4）：FIELD_ENCRYPTION_KEY 换新后，旧主密钥（SECRET_KEY）产出的
    存量 v3 密文仍可读（写新读旧），新写入用新主密钥。"""
    from django.conf import settings

    from common.base.utils import AESCipherV3

    old_master = settings.SECRET_KEY
    legacy_cipher_text = AESCipherV3(old_master).encrypt("存量密文")
    assert legacy_cipher_text.decode().startswith("v3:")

    rotated = AESCipherV3("new-master-key", legacy_masters=(old_master,))
    assert rotated.decrypt(legacy_cipher_text) == "存量密文"  # 读旧
    new_cipher = rotated.encrypt("新写入")
    assert rotated.decrypt(new_cipher) == "新写入"  # 写新
    # 旧主密钥的 signer 读不出新主密钥密文（单向：旧不能读新）
    with pytest.raises(ValueError):
        AESCipherV3(old_master).decrypt(new_cipher)


def test_legacy_master_chain_walks_in_order():
    """多级轮换：legacy_masters 按序逐个试钥，两级历史密文均可读。"""
    from common.base.utils import AESCipherV3

    k1, k2, k3 = "master-v1", "master-v2", "master-v3"
    c1 = AESCipherV3(k1).encrypt("一代")
    c2 = AESCipherV3(k2, legacy_masters=(k1,)).encrypt("二代")
    walker = AESCipherV3(k3, legacy_masters=(k2, k1))
    assert walker.decrypt(c1) == "一代"
    assert walker.decrypt(c2) == "二代"


def test_all_masters_failed_still_raises():
    """全部主密钥（含历史）认证失败：抛 ValueError，由调用方按解密失败降级。"""
    from common.base.utils import AESCipherV3

    cipher_text = AESCipherV3("master-a").encrypt("secret")
    walker = AESCipherV3("master-b", legacy_masters=("master-c",))
    with pytest.raises(ValueError):
        walker.decrypt(cipher_text)


def test_get_signer_uses_field_encryption_key_with_secret_fallback():
    """FIELD_ENCRYPTION_KEY 配置后：写用新主密钥，读回退 SECRET_KEY 存量。"""
    from django.conf import settings

    from common.base.utils import AESCipherV3, get_signer

    stock = AESCipherV3(settings.SECRET_KEY).encrypt("存量数据")  # 轮换前的存量 v3 密文
    settings.FIELD_ENCRYPTION_KEY = "dedicated-field-key"
    try:
        signer = get_signer()
        assert signer.decrypt(stock) == "存量数据"  # SECRET_KEY 作为历史主密钥兜底
        fresh = signer.encrypt("新数据")
        assert signer.decrypt(fresh) == "新数据"
        # 旧主密钥 signer 解不出新密文
        with pytest.raises(ValueError):
            AESCipherV3(settings.SECRET_KEY).decrypt(fresh)
    finally:
        settings.FIELD_ENCRYPTION_KEY = ""
