#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据治理工具：聚合只读清单 + 重加密 + 审计（管理命令与 API 共用）。

- ``credential_overview()``：Setting 加密项 / SystemConfig 敏感键 / 模型字段级凭据的
  只读聚合（名称 / 类型 / 存储形态 / 加密状态 / 最近更新时间），供「凭据与密钥」页；
- ``rotate_system_config`` / ``rotate_setting``：重加密（明文 → 首次加密；密文 → 轮换
  salt/nonce），并失效配置缓存、写 OperationLog(module=system:credential) 审计。
"""

from django.utils.translation import gettext_lazy as _

from common.core.credentials import (
    SENSITIVE_SETTING_KEYS,
    decrypt_setting_value,
    encrypt_setting_value,
    encryption_status,
    plaintext_sensitive_keys,
)
from common.utils import get_logger

logger = get_logger(__name__)

AUDIT_MODULE = "system:credential"


def _iso(value) -> str:
    return value.isoformat() if value else ""


def write_credential_audit(detail: dict, user=None) -> None:
    """凭据操作审计（module=system:credential）；失败只记日志不影响主流程。"""
    import json

    from system.models import OperationLog

    try:
        OperationLog.objects.create(
            module=AUDIT_MODULE,
            object_pk=str(detail.get("key") or ""),
            creator=user if getattr(user, "pk", None) else None,
            status_code=1000 if detail.get("ok", True) else 1001,
            response_code=1000 if detail.get("ok", True) else 1001,
            changes=json.dumps(detail, ensure_ascii=False, default=str)[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响轮换本身
        logger.warning("write credential audit failed", exc_info=True)


def credential_overview() -> dict:
    """凭据聚合清单（只读）：不返回任何密文或明文值，只给状态。"""
    from settings.models import Setting
    from system.models import SystemConfig

    settings_rows = [
        {
            "name": row.name,
            "category": row.category,
            "scope": "setting",
            "configured": bool(row.value),
            "encrypted": bool(row.encrypted),
            "updated_time": _iso(row.updated_time),
        }
        for row in Setting.objects.filter(encrypted=True).order_by("category", "name")
    ]
    config_rows = []
    for key, fields in SENSITIVE_SETTING_KEYS.items():
        row = SystemConfig.objects.filter(key=key).first()
        status = encryption_status(key, row.value if row else None)
        config_rows.append(
            {
                "name": key,
                "scope": "system_config",
                "fields": list(fields) or ["*"],
                "configured": status != "empty",
                "status": status,
                "encrypted": status in ("encrypted", "empty"),
                "updated_time": _iso(row.updated_time) if row else "",
                "description": (row.description if row else "") or "",
            }
        )
    model_rows = _model_credential_rows()
    return {
        "settings": settings_rows,
        "system_configs": config_rows,
        "model_fields": model_rows,
        "plaintext": plaintext_sensitive_keys(),
    }


def _model_credential_rows() -> list:
    """模型字段级凭据（值级加密，按「已配置数量」聚合，不暴露任何值）。"""
    from django.apps import apps

    rows = []
    checks = (
        ("system", "AiProfile", "api_key", _("AI profile API key")),
        ("system", "WebhookSubscription", "secret", _("Webhook signing secret")),
        ("system", "ApiApplication", "callback_secret_encrypted", _("Open platform callback secret")),
    )
    for app_label, model_name, field, label in checks:
        try:
            model = apps.get_model(app_label, model_name)
            configured = model.objects.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True}).count()
        except Exception:  # noqa: BLE001 模型缺失（模块裁剪）不阻断聚合
            continue
        rows.append(
            {
                "name": f"{model_name}.{field}",
                "label": str(label),
                "scope": "model_field",
                "configured_count": configured,
                "encrypted": True,
            }
        )
    return rows


def rotate_system_config(key: str, user=None) -> dict:
    """重加密（或首次加密）单个 SystemConfig 敏感键；返回 ``{ok, action, detail}``。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    key = str(key or "").strip()
    if key not in SENSITIVE_SETTING_KEYS:
        return {"ok": False, "action": "skip", "detail": str(_("Not a registered sensitive key: {}").format(key))}
    row = SystemConfig.objects.filter(key=key).first()
    if row is None:
        return {"ok": False, "action": "skip", "detail": str(_("The credential is not configured yet"))}
    status = encryption_status(key, row.value)
    if status == "empty":
        return {"ok": True, "action": "skip", "detail": str(_("The credential is not configured yet"))}
    action = "encrypt" if status == "plaintext" else "rotate"
    plain = decrypt_setting_value(key, row.value)
    row.value = encrypt_setting_value(key, plain)
    row.save(update_fields=["value", "updated_time"])
    SysConfig.invalid_config_cache(key=key)
    detail = {"key": key, "scope": "system_config", "action": action, "ok": True}
    write_credential_audit(detail, user=user)
    return {"ok": True, "action": action, "detail": ""}


def rotate_setting(name: str, user=None) -> dict:
    """重加密单个 Setting 加密项（encrypted=True）；返回 ``{ok, action, detail}``。"""
    from common.base.utils import signer
    from settings.models import Setting

    name = str(name or "").strip()
    row = Setting.objects.filter(name=name, encrypted=True).first()
    if row is None or not row.value:
        return {"ok": False, "action": "skip", "detail": str(_("The credential is not configured yet"))}
    try:
        plain = signer.decrypt(row.value)
        row.value = signer.encrypt(plain.encode("utf-8")).decode("utf-8")
        row.save(update_fields=["value", "updated_time"])
    except Exception as exc:  # noqa: BLE001 解密失败（密钥轮换/损坏）按失败返回
        logger.warning("rotate setting credential failed. name:%s", name, exc_info=True)
        detail = {"key": name, "scope": "setting", "action": "rotate", "ok": False, "error": str(exc)[:200]}
        write_credential_audit(detail, user=user)
        return {"ok": False, "action": "rotate", "detail": str(_("Credential re-encryption failed"))}
    detail = {"key": name, "scope": "setting", "action": "rotate", "ok": True}
    write_credential_audit(detail, user=user)
    return {"ok": True, "action": "rotate", "detail": ""}
