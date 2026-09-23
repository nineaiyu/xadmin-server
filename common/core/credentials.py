#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据治理：敏感配置键注册表 + 值级加密（signer v3）+ 巡检。

两类存储形态的加密口径：

1. **SystemConfig（本模块负责）**：``value`` 是 JSON（dict/list/标量），敏感键在
   ``SENSITIVE_SETTING_KEYS`` 中声明「需要加密的 JSON 字段」（空元组 = 整值加密）。
   写路径经 :func:`encrypt_setting_value`（幂等：已加密不重复加密），读路径经
   :func:`decrypt_setting_value`（明文兼容：历史明文照常可读，迁移不炸）。
2. **Setting（既有体系）**：由 ``Setting.encrypted`` 位 + 序列化器 ``write_only``
   声明驱动（``settings/views/settings.py`` 按 write_only 推断 encrypted）。
   ``ENCRYPTED_SETTING_KEYS`` 是声明清单，守护测试保证代码声明与清单不漂移。

``PLAINTEXT_EXEMPT_KEYS``：历史遗留明文豁免（**只减不增**，迁移完成后清空）。
巡检入口：``plaintext_sensitive_keys()``（供守护测试 / ``rotate_credential --audit``）。
"""

import re

from common.base.utils import signer
from common.utils import get_logger

logger = get_logger(__name__)

#: SystemConfig 敏感键 → 需要加密的 JSON 字段（空元组 = 整值加密）
SENSITIVE_SETTING_KEYS = {
    # 第三方登录 provider 列表：逐项 client_secret 字段加密（其余字段非敏感）
    "OAUTH_PROVIDERS": ("client_secret",),
    # 独立 Bearer / 回调令牌：整值加密
    "SCIM_TOKEN": (),
    "BACKUP_ALERT_TOKEN": (),
    "OPS_ALERT_TOKEN": (),
    # 对象存储访问凭据（可插拔存储后端）：整值加密
    "FILE_S3_ACCESS_KEY": (),
    "FILE_S3_SECRET_KEY": (),
}

#: 历史遗留明文豁免清单（只减不增；迁移完成后清空）
PLAINTEXT_EXEMPT_KEYS: set = set()

#: Setting 体系中声明为加密（write_only）的敏感键清单（守护测试比对两侧）
ENCRYPTED_SETTING_KEYS = (
    "AI_API_KEY",
    "LDAP_BIND_PASSWORD",
    "EMAIL_HOST_PASSWORD",
    "ALIBABA_ACCESS_KEY_SECRET",
    "DINGTALK_APP_SECRET",
    "WECOM_CORP_SECRET",
    "FEISHU_APP_SECRET",
)

#: 敏感键名模式（守护测试用：新配置键命中模式必须显式声明加密或豁免）
SENSITIVE_KEY_PATTERN = re.compile(r"(password|passwd|secret|token|api_?key|access_key|private_key)", re.I)

#: 字段级加密前缀（AESCipherV3：v3: + base64(salt|nonce|ct|tag)）
CIPHER_PREFIX = "v3:"


def is_sensitive_key(key: str) -> bool:
    """该 SystemConfig 键是否声明为敏感。"""
    return str(key or "") in SENSITIVE_SETTING_KEYS


def is_cipher_str(value) -> bool:
    """值是否为本仓字段级密文（v3: 前缀）。"""
    return isinstance(value, str) and value.startswith(CIPHER_PREFIX)


def _is_empty(value) -> bool:
    """空值判定（None / 空串 / 空容器一律视为未配置）。"""
    return value is None or value == "" or value == [] or value == {}


def _collect_field_values(value, fields) -> list:
    """按字段声明收集需要检查/加密的标量值（空元组 = 整值）。"""
    if not fields:
        return [] if _is_empty(value) else [value]
    if isinstance(value, list):
        return [item.get(field) for item in value if isinstance(item, dict) for field in fields if item.get(field)]
    if isinstance(value, dict):
        return [value.get(field) for field in fields if value.get(field)]
    return []


def _map_field_values(value, fields, mapper):
    """对敏感字段值应用 mapper（幂等/兼容由 mapper 保证）。"""
    if not fields:
        return mapper(value) if value not in (None, "") else value
    if isinstance(value, list):
        return [_map_dict(item, fields, mapper) for item in value]
    if isinstance(value, dict):
        return _map_dict(value, fields, mapper)
    return value


def _map_dict(item, fields, mapper):
    if not isinstance(item, dict):
        return item
    result = dict(item)
    for field in fields:
        if result.get(field):
            result[field] = mapper(result[field])
    return result


def _encrypt_scalar(value):
    """单值加密（幂等：已是 v3 密文原样返回）。"""
    if not isinstance(value, str) or not value:
        return value
    if is_cipher_str(value):
        return value
    return signer.encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_scalar(value):
    """单值解密（明文兼容：非 v3 前缀原样返回）。"""
    if not is_cipher_str(value):
        return value
    try:
        return signer.decrypt(value)
    except Exception:  # noqa: BLE001 密钥不匹配/密文损坏：原样返回由消费方降级
        logger.warning("credential decrypt failed, keep cipher text", exc_info=True)
        return value


def encrypt_setting_value(key: str, value):
    """SystemConfig 写入前加密敏感字段（非敏感键 / 空值原样返回）。"""
    fields = SENSITIVE_SETTING_KEYS.get(str(key or ""))
    if fields is None:
        return value
    try:
        return _map_field_values(value, fields, _encrypt_scalar)
    except Exception:  # noqa: BLE001 加密失败不阻断写入（由巡检暴露明文）
        logger.warning("credential encrypt failed. key:%s", key, exc_info=True)
        return value


def decrypt_setting_value(key: str, value):
    """SystemConfig 读取后解密敏感字段（非敏感键 / 明文原样返回）。"""
    fields = SENSITIVE_SETTING_KEYS.get(str(key or ""))
    if fields is None:
        return value
    return _map_field_values(value, fields, _decrypt_scalar)


def encryption_status(key: str, value) -> str:
    """敏感键的加密状态：not_sensitive / empty / encrypted / plaintext。"""
    fields = SENSITIVE_SETTING_KEYS.get(str(key or ""))
    if fields is None:
        return "not_sensitive"
    values = _collect_field_values(value, fields)
    if not values:
        return "empty"
    return "encrypted" if all(is_cipher_str(item) for item in values) else "plaintext"


def plaintext_sensitive_keys() -> list:
    """巡检：返回当前库中仍为明文的敏感 SystemConfig 键（排除豁免清单）。

    只读、异常降级为空清单（库未就绪时不阻断调用方）。
    """
    try:
        from system.models import SystemConfig
    except Exception:  # noqa: BLE001 模型不可用（迁移期）不巡检
        return []
    offenders = []
    try:
        rows = SystemConfig.objects.filter(key__in=list(SENSITIVE_SETTING_KEYS)).values_list("key", "value")
    except Exception:  # noqa: BLE001 查询失败不阻断
        return []
    for key, value in rows:
        if key in PLAINTEXT_EXEMPT_KEYS:
            continue
        if encryption_status(key, value) == "plaintext":
            offenders.append(key)
    return sorted(offenders)
