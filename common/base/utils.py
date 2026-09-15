#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : utils
# author : ly_13
# date : 6/2/2023
import base64
import hashlib
import os

from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Hash import SHA256
from Cryptodome.Protocol.KDF import HKDF
from django.conf import settings
from django.forms.models import ModelChoiceIteratorValue

from common.utils import get_logger

logger = get_logger(__name__)


class AESCipher:
    def __init__(self, key):
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, raw: bytes | str) -> bytes:
        raw = self._pack_data(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw))

    def decrypt(self, enc: str | bytes) -> str:
        enc = base64.b64decode(enc)
        iv = enc[: AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpack_data(cipher.decrypt(enc[AES.block_size :]))

    @staticmethod
    def _pack_data(s):
        if isinstance(s, str):
            s = s.encode("utf-8")
        return s + ((AES.block_size - len(s) % AES.block_size) * chr(AES.block_size - len(s) % AES.block_size)).encode(
            "utf-8"
        )

    @staticmethod
    def _unpack_data(s):
        data = s[: -ord(s[len(s) - 1 :])]
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return data


class AESCipherV3:
    """字段级加密 v3（S5）：HKDF 派生独立数据密钥 + AES-256-GCM，写新读旧。

    动机：旧 ``AESCipher`` 以 ``sha256(SECRET_KEY)`` 直接派生、AES-CBC 无完整性校验——
    SECRET_KEY 一处泄露即可同时伪造 JWT 并解密全部落库密钥，且密文可被篡改。

    格式：``v3:`` + base64(salt[16] | nonce[12] | ct | tag[16])

    - 数据密钥 = HKDF-SHA256(master=SECRET_KEY, salt=每次随机, info="xadmin-field-encryption")，
      与 JWT 签名/前端传输加密（``AESCipherV2`` 的 PBKDF2 域）密钥隔离，互不派生；
    - AES-256-GCM 同时提供机密性与完整性：密文篡改/错误密钥在解密时抛异常。

    向后兼容：``decrypt`` 对无 ``v3:`` 前缀的旧 CBC 密文回退旧实现解析，
    存量数据无需迁移（下次写入自动升级为 v3 格式）。
    """

    PREFIX = "v3:"
    SALT_LENGTH = 16
    NONCE_LENGTH = 12
    TAG_LENGTH = 16
    KEY_LENGTH = 32
    HKDF_INFO = b"xadmin-field-encryption"

    def __init__(self, key: str | bytes):
        self.key = key.encode("utf-8") if isinstance(key, str) else key
        self._legacy = AESCipher(key.decode("utf-8") if isinstance(key, bytes) else key)

    def _derive_key(self, salt: bytes) -> bytes:
        return HKDF(
            master=self.key,
            key_len=self.KEY_LENGTH,
            salt=salt,
            hashmod=SHA256,
            context=self.HKDF_INFO,
        )

    def encrypt(self, raw: bytes | str) -> bytes:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        salt = Random.new().read(self.SALT_LENGTH)
        nonce = Random.new().read(self.NONCE_LENGTH)
        cipher = AES.new(self._derive_key(salt), AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(raw)
        payload = base64.b64encode(salt + nonce + ciphertext + tag)
        return self.PREFIX.encode("utf-8") + payload

    def decrypt(self, enc: str | bytes) -> str:
        text = enc.decode("utf-8", "ignore") if isinstance(enc, bytes) else enc
        if not text.startswith(self.PREFIX):
            # 旧格式（v1 CBC）回落：存量密文继续可读
            return self._legacy.decrypt(enc)
        data = base64.b64decode(text[len(self.PREFIX) :])
        salt = data[: self.SALT_LENGTH]
        nonce = data[self.SALT_LENGTH : self.SALT_LENGTH + self.NONCE_LENGTH]
        body = data[self.SALT_LENGTH + self.NONCE_LENGTH :]
        cipher = AES.new(self._derive_key(salt), AES.MODE_GCM, nonce=nonce)
        # 认证失败（篡改/错钥）抛 ValueError，由调用方按解密失败处理
        plain = cipher.decrypt_and_verify(body[: -self.TAG_LENGTH], body[-self.TAG_LENGTH :])
        return plain.decode("utf-8")


def get_signer():
    """字段级加解密器（S5）：写路径统一 v3（HKDF + AES-GCM），读路径兼容旧 v1 密文。"""
    return AESCipherV3(settings.SECRET_KEY)


signer: AESCipherV3 = get_signer()


class AesBaseCrypt:
    def __init__(self):
        self.cipher = AESCipher(self.__class__.__name__)

    def set_encrypt_uid(self, key):
        return self.cipher.encrypt(key.encode("utf-8")).decode("utf-8")

    def get_decrypt_uid(self, enc):
        try:
            return self.cipher.decrypt(enc)
        except Exception as e:
            logger.warning(f"decrypt {enc} failed. exception:{e}")


def get_choices_dict(choices, disabled_choices=None):
    result = []
    choices_org_list = list(choices)
    for choice in choices_org_list:
        c0 = choice[0]
        if isinstance(c0, ModelChoiceIteratorValue):
            c0 = str(c0)
        val = {"value": c0, "label": choice[1]}
        if disabled_choices and isinstance(disabled_choices, list) and choice[0] in disabled_choices:
            val["disabled"] = True
        result.append(val)
    return result


def get_choices_name_from_key(choices, key):
    choices_org_list = list(choices)
    for choice in choices_org_list:
        if choice[0] == key:
            return choice[1]
    return ""


def redis_key_func(key, key_prefix, version):
    """
    Default function to generate keys.

    Construct the key used by all other methods. By default, prepend
    the `key_prefix`. KEY_FUNCTION can be used to specify an alternate
    function with custom key making behavior.
    """
    return key


def redis_reverse_key_func(key: str) -> str:
    return key


def menu_list_to_tree(data: list, root_field: str = "parent") -> list:
    """
    将权限菜单转换为树状结构
    """
    mapping: dict = dict(zip([str(i["pk"]) for i in data], data, strict=True))

    # 树容器
    container: list = []

    for d in data:
        # 如果找不到父级项，则是根节点
        parent = d.get(root_field)
        if isinstance(parent, dict) and "pk" in parent:
            parent = parent.get("pk")
        parent: dict = mapping.get(str(parent))
        if parent is None:
            container.append(d)
        else:
            children: list = parent.get("children")
            if not children:
                children = []
            children.append(d)
            parent.update({"children": children, "count": len(children)})
    return container


def format_menu_meta(meta: dict) -> dict:
    new_meta = {}
    for key in ["icon", "title", "rank", "showLink"]:
        new_meta[key] = meta.get(key)
    return new_meta


def format_menu_data(data):
    new_result = []
    for d in data:
        if d.get("count", -1) < 1:
            route = {
                "path": f"/default{d.get('path')}",
                "title": d.get("title"),
                "meta": format_menu_meta(d.get("meta", {})),
                "children": [d],
            }
        else:
            route = d
        new_result.append(route)
    return new_result


def remove_file(name):
    try:
        if os.path.isdir(name):
            os.rmdir(name)
        else:
            os.remove(name)
        logger.info(f"remove {name} success")
    except Exception as e:
        # FileNotFoundError is raised if the file or directory was removed
        # concurrently.
        logger.warning(f"remove {name} failed {e}")


class AESCipherV2:
    """
    前端凭证加密解密，双格式自适应：

    - 旧格式：OpenSSL ``Salted__`` 兼容格式（EVP_BytesToKey(MD5) + AES-256-CBC），
      无前缀，由前端 crypto-es（原 crypto-js）产出；
    - v2 格式：以 ``v2:`` 前缀标记，payload 为 base64(salt[16] | iv[12] | ct | tag[16])，
      PBKDF2-HMAC-SHA256（100k 迭代）派生 AES-256-GCM 密钥，与浏览器 WebCrypto
      subtle 对齐。

    加密端（前端）优先 v2、无 WebCrypto 环境回退旧格式；解密端按前缀自适应，
    认证失败/格式非法统一返回空串（与旧格式非法输入行为一致）。

    旧格式前端操作（已由 crypto-es 替换，协议不变）：
    import CryptoJS from "crypto-js";

    export function AesEncrypted(key: string, msg: string): string {
      return CryptoJS.AES.encrypt(msg, key).toString();
    }

    export function AesDecrypted(key: string, encryptedMessage: string): string {
      return CryptoJS.AES.decrypt(encryptedMessage, key).toString(
        CryptoJS.enc.Utf8
      );
    }
    """

    V2_PREFIX = "v2:"
    V2_SALT_LENGTH = 16
    V2_IV_LENGTH = 12
    V2_TAG_LENGTH = 16
    V2_PBKDF2_ITERATIONS = 100_000
    V2_KEY_LENGTH = 32

    def __init__(self, key: str | bytes):
        self.key = key.encode("utf-8") if isinstance(key, str) else key

    def _make_key(self, salt, output=48):
        key = hashlib.md5(self.key + salt).digest()
        final_key = key
        while len(final_key) < output:
            key = hashlib.md5(key + self.key + salt).digest()
            final_key += key
        return final_key[:output]

    def encrypt(self, raw):
        salt = Random.new().read(8)
        key_iv = self._make_key(salt, 32 + 16)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return base64.b64encode(b"Salted__" + salt + cipher.encrypt(self._pack_data(raw)))

    def decrypt(self, enc: str | bytes) -> str:
        text = enc.decode("utf-8", "ignore") if isinstance(enc, bytes) else enc
        if text.startswith(self.V2_PREFIX):
            return self._decrypt_v2(text[len(self.V2_PREFIX) :])
        if not self._v1_decrypt_enabled():
            # 灰度开关关闭后拒绝旧格式，与非法输入同语义返回空串
            return ""
        data = base64.b64decode(enc)
        if data[:8] != b"Salted__":
            return ""
        # v1 退役观测点（发布窗口 checklist 前置条件的核验依据）：仅对「合法旧格式密文」
        # 留痕，运维按该标记确认观察窗口内命中清零后再关闭 SECURITY_AES_V1_DECRYPT_ENABLED；
        # 非 Salted__ 的任意输入不会触发本日志，避免日志放大。
        logger.warning("aes_v1_decrypt_used: 旧格式（Salted__）密文命中，v1 退役观察期标记")
        salt = data[8:16]
        key_iv = self._make_key(salt, 32 + 16)
        key = key_iv[:32]
        iv = key_iv[32:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return self._unpack_data(cipher.decrypt(data[AES.block_size :]))

    def _decrypt_v2(self, payload_b64: str) -> str:
        try:
            data = base64.b64decode(payload_b64)
            salt = data[: self.V2_SALT_LENGTH]
            iv = data[self.V2_SALT_LENGTH : self.V2_SALT_LENGTH + self.V2_IV_LENGTH]
            body = data[self.V2_SALT_LENGTH + self.V2_IV_LENGTH :]
            if len(body) <= self.V2_TAG_LENGTH:
                return ""
            key = hashlib.pbkdf2_hmac("sha256", self.key, salt, self.V2_PBKDF2_ITERATIONS, dklen=self.V2_KEY_LENGTH)
            cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
            plain = cipher.decrypt_and_verify(body[: -self.V2_TAG_LENGTH], body[-self.V2_TAG_LENGTH :])
            return plain.decode("utf-8")
        except Exception:
            # GCM 认证失败 / 格式非法：返回空串，交由业务层按解密失败处理
            return ""

    @staticmethod
    def _v1_decrypt_enabled() -> bool:
        """旧格式（Salted__）解密灰度开关（SECURITY_AES_V1_DECRYPT_ENABLED）。

        默认开启保持存量前端兼容；配置缺失（Settings 尚未加载的极端场景）按开启处理，
        宁可多兼容不误杀。确认全量用户升级至 v2 优先前端后由运维关闭。
        """
        try:
            from django.conf import settings

            return bool(getattr(settings, "SECURITY_AES_V1_DECRYPT_ENABLED", True))
        except Exception:
            return True

    @staticmethod
    def _pack_data(s):
        return s + ((AES.block_size - len(s) % AES.block_size) * chr(AES.block_size - len(s) % AES.block_size)).encode(
            "utf-8"
        )

    @staticmethod
    def _unpack_data(s):
        data = s[: -ord(s[len(s) - 1 :])]
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return data
