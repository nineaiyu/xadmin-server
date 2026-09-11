# -*- coding: utf-8 -*-
"""集成测试用 AES v2 协议加密助手（模拟浏览器 WebCrypto PBKDF2+AES-GCM 产出）。"""

import base64
import hashlib

from Cryptodome.Cipher import AES
from Cryptodome.Random import get_random_bytes

from common.base.utils import AESCipherV2


def encrypt_v2(key: str, plain: str) -> str:
    """按 v2 协议构造密文：v2: + base64(salt[16] | iv[12] | ct | tag[16])。"""
    salt = get_random_bytes(16)
    iv = get_random_bytes(12)
    derived = hashlib.pbkdf2_hmac("sha256", key.encode(), salt, 100_000, dklen=32)
    cipher = AES.new(derived, AES.MODE_GCM, nonce=iv)
    body, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return AESCipherV2.V2_PREFIX + base64.b64encode(salt + iv + body + tag).decode()
