#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据轮换动作（系统配置键 / 模型密钥字段的再生成与轮换，自 credential 拆分，行为不变）。"""

import secrets

from django.utils.translation import gettext_lazy as _

from common.core.credentials import (
    SENSITIVE_SETTING_KEYS,
    decrypt_setting_value,
    encrypt_setting_value,
    encryption_status,
)
from common.utils import get_logger
from system.utils.credential import (
    MODEL_CREDENTIAL_FIELDS,
    NOT_CONFIGURED_DETAIL,
    NOT_ROTATABLE_DETAIL,
    ROTATABLE_SYSTEM_CONFIG_KEYS,
    write_credential_audit,
)

logger = get_logger(__name__)


def regenerate_system_config(key: str, user=None) -> dict:
    """原地轮换系统自生成的 SystemConfig 键：重新生成随机值并加密落库。

    外部签发的注册键（OAuth/S3）拒绝原地轮换，返回更换入口提示，避免造假值打挂集成。
    返回 ``{ok, action, detail}``。
    """
    from common.core.config import SysConfig
    from system.models import SystemConfig

    key = str(key or "").strip()
    if key not in ROTATABLE_SYSTEM_CONFIG_KEYS:
        return {"ok": False, "action": "skip", "detail": str(NOT_ROTATABLE_DETAIL)}
    row = SystemConfig.objects.filter(key=key).first()
    if row is None or encryption_status(key, row.value) == "empty":
        return {"ok": False, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    row.value = encrypt_setting_value(key, secrets.token_urlsafe(32))
    row.save(update_fields=["value", "updated_time"])
    SysConfig.invalid_config_cache(key=key)
    detail = {"key": key, "scope": "system_config", "action": "rotate", "ok": True}
    write_credential_audit(detail, user=user)
    return {"ok": True, "action": "rotate", "detail": ""}


def rotate_model_field(name: str, user=None) -> dict:
    """原地轮换白名单内的模型字段级凭据：逐行重新生成随机值并加密落库。

    只接受 :data:`MODEL_CREDENTIAL_FIELDS` 中的键且 ``rotatable=True``，杜绝任意
    模型字段注入；Webhook / 回调密钥变更会影响对端验签，调用方（视图）负责二次确认。
    返回 ``{ok, action, detail, count}``。
    """
    from django.apps import apps

    from system.utils.webhook import encrypt_secret

    name = str(name or "").strip()
    meta = MODEL_CREDENTIAL_FIELDS.get(name)
    if meta is None or not meta.get("rotatable"):
        return {"ok": False, "action": "skip", "detail": str(NOT_ROTATABLE_DETAIL)}
    try:
        model = apps.get_model(meta["app_label"], meta["model"])
    except Exception:  # noqa: BLE001 模型缺失（模块裁剪）不处理
        logger.warning("rotate model credential skipped, model missing. name:%s", name)
        return {"ok": False, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    field = meta["field"]
    queryset = model.objects.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True})
    count = 0
    for row in queryset:
        setattr(row, field, encrypt_secret(_model_secret_plaintext(name)))
        row.save(update_fields=[field, "updated_time"])
        count += 1
    detail = {"key": name, "scope": "model_field", "action": "rotate", "ok": True, "count": count}
    write_credential_audit(detail, user=user)
    if count == 0:
        return {"ok": True, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    return {"ok": True, "action": "rotate", "detail": "", "count": count}


def _model_secret_plaintext(name: str) -> str:
    """生成模型字段级凭据的新明文（回调密钥沿用 ``apc_`` 前缀，与创建路径同口径）。"""
    raw = secrets.token_urlsafe(32)
    if name == "ApiApplication.callback_secret_encrypted":
        return f"apc_{raw}"
    return raw


def rotate_system_config(key: str, user=None) -> dict:
    """重加密（或首次加密）单个 SystemConfig 敏感键；返回 ``{ok, action, detail}``。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    key = str(key or "").strip()
    if key not in SENSITIVE_SETTING_KEYS:
        return {"ok": False, "action": "skip", "detail": str(_("Not a registered sensitive key: {}").format(key))}
    row = SystemConfig.objects.filter(key=key).first()
    if row is None:
        return {"ok": False, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    status = encryption_status(key, row.value)
    if status == "empty":
        return {"ok": True, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    action = "encrypt" if status == "plaintext" else "rotate"
    plain = decrypt_setting_value(key, row.value)
    row.value = encrypt_setting_value(key, plain)
    row.save(update_fields=["value", "updated_time"])
    SysConfig.invalid_config_cache(key=key)
    detail = {"key": key, "scope": "system_config", "action": action, "ok": True}
    write_credential_audit(detail, user=user)
    return {"ok": True, "action": action, "detail": ""}


def rotate_setting(name: str, user=None) -> dict:
    """重加密/首次加密单个 Setting 敏感项；返回 ``{ok, action, detail}``。

    两种输入形态（值一律不变，仅加密态收敛）：

    - ``encrypted=True``：解密后重加密（轮换 salt/nonce），``action=rotate``；
    - **明文行（``encrypted=False``）**：把库内 JSON 文本原样加密并置 encrypted=True，
      ``action=encrypt``——``plaintext_setting_names()`` 检出的存量明文由此可修复
      （``rotate_credential`` 命令口径与 SystemConfig 侧一致）；
    - 值已是 v3 密文但标记为明文（标记漂移）：只校正 encrypted 标记，不重复加密。
    """
    from common.base.utils import signer
    from common.core.credentials import is_cipher_str
    from settings.models import Setting

    name = str(name or "").strip()
    row = Setting.objects.filter(name=name).first()
    if row is None or not row.value:
        return {"ok": False, "action": "skip", "detail": str(NOT_CONFIGURED_DETAIL)}
    try:
        if not row.encrypted and is_cipher_str(row.value):
            # 标记漂移：值已是密文，重复加密会让 cleared_value 解出内层密文
            row.encrypted = True
            row.save(update_fields=["encrypted", "updated_time"])
            action = "fix_flag"
        elif row.encrypted:
            plain = signer.decrypt(row.value)
            row.value = signer.encrypt(plain).decode("utf-8")
            row.save(update_fields=["value", "updated_time"])
            action = "rotate"
        else:
            row.value = signer.encrypt(row.value.encode("utf-8")).decode("utf-8")
            row.encrypted = True
            row.save(update_fields=["value", "encrypted", "updated_time"])
            action = "encrypt"
    except Exception as exc:  # noqa: BLE001 解密失败（密钥轮换/损坏）按失败返回
        logger.warning("rotate setting credential failed. name:%s", name, exc_info=True)
        detail = {"key": name, "scope": "setting", "action": "rotate", "ok": False, "error": str(exc)[:200]}
        write_credential_audit(detail, user=user)
        return {"ok": False, "action": "rotate", "detail": str(_("Credential re-encryption failed"))}
    detail = {"key": name, "scope": "setting", "action": action, "ok": True}
    write_credential_audit(detail, user=user)
    return {"ok": True, "action": action, "detail": ""}
