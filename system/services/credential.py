#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据治理服务：聚合只读清单 + 轮换/重加密 + 审计（管理命令与 API 共用）。

归位为 system 服务层：本模块承担落库、审计与配置缓存失效等业务副作用，
视图 / 管理命令只做请求解析与响应构造。

两类动作语义必须区分，不能互相冒充：

- **原地轮换**（``regenerate_system_config`` / ``rotate_model_field``）：仅用于服务端
  自生成的密钥，重新生成随机值并加密落库——旧值立即失效，外部集成需同步更新；
- **重加密**（``rotate_system_config`` / ``rotate_setting``）：明文 → 首次加密、密文 →
  轮换 salt/nonce，**值不变**，供 ``rotate_credential`` 命令做明文修复/迁移。

外部签发的凭据（模型厂商 api_key、OAuth client_secret、S3 密钥、邮箱/IM 密钥等）**不提供
原地轮换**（会生成对端不认的假值、打挂集成），只在总览里标注「更换入口」；``credential_overview()``
**不回传任何明文或可解密值**。所有写动作失效配置缓存并写 OperationLog(module=system:credential)。
"""

from typing import Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.auth.credentials import (
    MASK,
    SENSITIVE_SETTING_KEYS,
    encryption_status,
    plaintext_sensitive_keys,
    plaintext_setting_names,
)
from common.core.response import API_SUCCESS_CODE

AUDIT_MODULE = "system:credential"

#: 系统自生成、可原地重生的 SystemConfig 键（轮换 = 重新生成随机值后加密落库）
ROTATABLE_SYSTEM_CONFIG_KEYS = ("SCIM_TOKEN", "BACKUP_ALERT_TOKEN", "OPS_ALERT_TOKEN")

#: 外部签发、只能替换的 SystemConfig 键 → 更换入口（去对应配置页更换，不提供轮换）
REPLACE_ONLY_SYSTEM_CONFIG_KEYS = {
    "OAUTH_PROVIDERS": "/system/config/system/index",
    "FILE_S3_ACCESS_KEY": "/system/config/system/index",
    "FILE_S3_SECRET_KEY": "/system/config/system/index",
}

#: Setting 分类 → 更换入口（Setting 体系凭据均为外部签发，只能去对应设置页替换）
SETTING_CATEGORY_ENTRY = {
    "ai": "/integration/ai/config",
    "ldap": "/settings/ldap",
    "email": "/settings/message",
    "notify_im": "/settings/message",
    "sms": "/settings/sms",
    "basic": "/settings/basic",
}
DEFAULT_SETTING_ENTRY = "/settings/ledger/index"

#: 模型字段级凭据白名单（轮换入参只认这里的键，杜绝任意模型字段注入）。
#: ``rotatable=True`` 仅限服务端生成、可安全重生的密钥；外部签发字段只标注更换入口。
MODEL_CREDENTIAL_FIELDS = {
    "AiProfile.api_key": {
        "app_label": "ai",
        "model": "AiProfile",
        "field": "api_key",
        "label": _("AI profile API key"),
        "rotatable": False,
        "change_entry": "/integration/ai/config",
    },
    "WebhookSubscription.secret": {
        "app_label": "task",
        "model": "WebhookSubscription",
        "field": "secret",
        "label": _("Webhook signing secret"),
        "rotatable": True,
        "change_entry": "",
    },
    "ApiApplication.callback_secret_encrypted": {
        "app_label": "identity",
        "model": "ApiApplication",
        "field": "callback_secret_encrypted",
        "label": _("Open platform callback secret"),
        "rotatable": True,
        "change_entry": "",
    },
}

#: 不可原地轮换的统一提示（视图/命令复用同一文案口径）
NOT_ROTATABLE_DETAIL = _("This credential cannot be rotated in place; replace it on its config page")
NOT_CONFIGURED_DETAIL = _("The credential is not configured yet")

#: 建议轮换阈值（天）：自生成密钥超过该时长未轮换（或从未轮换）时在总览标记提醒。
#: 只做提醒不强制（轮换会使旧值立即失效，外部集成需同步更新，节奏由运维定）。
CREDENTIAL_ROTATE_SUGGEST_DAYS = 90

#: 凭据用途提示（使用方）：静态知识随总览下发，这把钥匙给谁用、干什么不用猜
SYSTEM_CONFIG_USAGE_HINTS = {
    "SCIM_TOKEN": _("Used by SCIM 2.0 directory clients for provisioning API access"),
    "BACKUP_ALERT_TOKEN": _("Used by database backup jobs to report failures"),
    "OPS_ALERT_TOKEN": _("Used by ops scripts to send alert notifications"),
    "OAUTH_PROVIDERS": _("Used by third-party login (OAuth2/OIDC) providers"),
    "FILE_S3_ACCESS_KEY": _("Used by file storage (S3-compatible object store)"),
    "FILE_S3_SECRET_KEY": _("Used by file storage (S3-compatible object store)"),
}
MODEL_CREDENTIAL_USAGE_HINTS = {
    "AiProfile.api_key": _("Used by AI assistant / knowledge base model calls"),
    "WebhookSubscription.secret": _("Used to sign outgoing webhook deliveries"),
    "ApiApplication.callback_secret_encrypted": _("Used by open-platform apps for callback signature"),
}
SETTING_CATEGORY_USAGE_HINTS = {
    "ai": _("Used by AI assistant / knowledge base model calls"),
    "ldap": _("Used by LDAP/AD directory binding and sync"),
    "email": _("Used by SMTP mail sending"),
    "notify_im": _("Used by IM notifications (WeCom/DingTalk/Feishu/Slack)"),
    "sms": _("Used by SMS provider for verification codes and notifications"),
    "basic": _("Used by platform base settings"),
}


def _timestamp(value: Any) -> str:
    """最近更新时间的展示格式（与 DRF 的 DATETIME_FORMAT 同口径，前端无需再格式化）。"""
    if not value:
        return ""
    try:
        typed_value: str = timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S")
        return typed_value
    except Exception:  # noqa: BLE001 无时区信息等异常值回退 iso 字符串
        return str(value)[:19].replace("T", " ")


#: 轮换时间回溯的每键审计窗口（条）：action 在 changes JSON 内（无法 SQL 过滤），
#: 窗口过小会被近期 encrypt/其他动作挤掉而漏掉真正的轮换行；超出窗口按未知处理。
ROTATION_AUDIT_WINDOW = 200


def _last_rotated_times(keys: Any) -> dict[str, Any]:
    """一组凭据键的最近一次成功「原地轮换」时间（单次审计查询批量回溯）。

    审计行由 :func:`write_credential_audit` 落库（object_pk=凭据键，changes JSON 带
    action），重加密（encrypt 动作）不算轮换——值没变，不产生新的安全有效期。
    总览不再逐键查询（键数增长时线性劣化）：一次查询按时间倒序流式扫描，
    每键命中轮换行或扫满 :data:`ROTATION_AUDIT_WINDOW` 条即收口，与逐键口径一致。
    """
    import json

    from audit.services import OperationLog

    result: dict[str, Any] = {key: None for key in keys}
    if not result:
        return result
    scanned: dict[str, int] = dict.fromkeys(result, 0)
    rows = (
        OperationLog.objects.filter(module=AUDIT_MODULE, object_pk__in=result, response_code=API_SUCCESS_CODE)
        .order_by("-created_time")
        .values_list("object_pk", "changes", "created_time")
        .iterator()
    )
    for object_pk, changes, created in rows:
        if result.get(object_pk) is not None:
            continue
        scanned[object_pk] += 1
        if scanned[object_pk] <= ROTATION_AUDIT_WINDOW:
            try:
                detail = json.loads(changes or "{}")
            except Exception:  # noqa: BLE001 非法历史行跳过
                continue
            if detail.get("action") == "rotate" and detail.get("ok", True):
                result[object_pk] = created
        if all(result[key] is not None or scanned[key] >= ROTATION_AUDIT_WINDOW for key in result):
            break
    return result


def _rotation_fields(*, rotatable: bool, configured: bool, last: Any = None) -> dict[str, Any]:
    """总览行的轮换追踪字段：上次轮换时间 + 建议轮换标记（仅自生成可轮换键有意义）。"""
    if not rotatable:
        return {"last_rotated": "", "rotate_overdue": False}
    overdue = configured and (last is None or (timezone.now() - last).days >= CREDENTIAL_ROTATE_SUGGEST_DAYS)
    return {"last_rotated": _timestamp(last), "rotate_overdue": overdue}


def write_credential_audit(detail: dict[str, Any], user: Any = None) -> None:
    """凭据操作审计（module=system:credential）；失败只记日志不影响主流程。

    落库统一走 :func:`audit.services.record`（changes 序列化/截断、creator
    解析、异常只告警均内建）；本函数保留签名作为薄委托，调用面不变。
    """
    from audit.services import record

    ok_code = API_SUCCESS_CODE if detail.get("ok", True) else 1001
    record(
        module=AUDIT_MODULE,
        object_pk=str(detail.get("key") or ""),
        creator=user,
        status_code=ok_code,
        response_code=ok_code,
        changes=detail,
    )


def credential_overview() -> dict[str, Any]:
    """凭据聚合清单（只读）：不返回任何密文或明文值，只给状态与可运维动作。"""
    plaintext = sorted(set(plaintext_sensitive_keys()) | {f"Setting:{name}" for name in plaintext_setting_names()})
    # 轮换时间单次批量回溯（system_config 与 model_field 两侧可轮换键合并成一次审计查询）
    rotation = _last_rotated_times(
        [key for key in SENSITIVE_SETTING_KEYS if key in ROTATABLE_SYSTEM_CONFIG_KEYS]
        + [name for name, meta, _model in _resolved_model_credentials() if meta.get("rotatable")]
    )
    return {
        "settings": _setting_rows(),
        "system_configs": _system_config_rows(rotation),
        "model_fields": _model_credential_rows(rotation),
        "plaintext": plaintext,
    }


def _setting_rows() -> list[Any]:
    """Setting 凭据行：加密项 + 名字命中敏感模式的**明文行**（后者标红提示）。

    Setting 体系的敏感键均为外部签发，只能去对应设置页替换；明文行（历史遗留 /
    直改库）可由 ``rotate_credential`` 命令就地加密修复，故一并列在总览里。
    """
    from django.db.models import Q

    from settings.models import Setting

    plaintext_names = plaintext_setting_names()
    queryset = Setting.objects.filter(Q(encrypted=True) | Q(name__in=plaintext_names)).order_by("category", "name")
    rows = []
    for row in queryset:
        configured = bool(row.value)
        plaintext = not row.encrypted
        rows.append(
            {
                "name": row.name,
                "scope": "setting",
                "label": row.name,
                "category": row.category,
                "description": "",
                "configured": configured,
                "encrypted": bool(row.encrypted),
                "plaintext": plaintext,
                "status": "plaintext" if plaintext and configured else ("empty" if not configured else "encrypted"),
                "rotatable": False,
                "change_entry": SETTING_CATEGORY_ENTRY.get(row.category, DEFAULT_SETTING_ENTRY),
                "used_by": str(SETTING_CATEGORY_USAGE_HINTS.get(row.category, "")),
                "masked": MASK if configured else "",
                "updated_time": _timestamp(row.updated_time),
            }
        )
    return rows


def _resolved_model_credentials() -> list[tuple[str, dict[str, Any], Any]]:
    """MODEL_CREDENTIAL_FIELDS 解析出 (name, meta, model)；模块裁剪缺失的模型跳过。"""
    from django.apps import apps

    resolved = []
    for name, meta in MODEL_CREDENTIAL_FIELDS.items():
        try:
            resolved.append((name, meta, apps.get_model(meta["app_label"], meta["model"])))
        except Exception:  # noqa: BLE001 模型缺失（模块裁剪）不阻断聚合
            continue
    return resolved


def _system_config_rows(rotation: dict[str, Any]) -> list[Any]:
    """SystemConfig 敏感键：区分「自生成可轮换」与「外部签发只能替换」。"""
    from system.models import SystemConfig

    configs = SystemConfig.objects.filter(key__in=SENSITIVE_SETTING_KEYS).in_bulk(field_name="key")
    rows = []
    for key, fields in SENSITIVE_SETTING_KEYS.items():
        row = configs.get(key)
        status = encryption_status(key, row.value if row else None)
        configured = status != "empty"
        rotatable = key in ROTATABLE_SYSTEM_CONFIG_KEYS
        rows.append(
            {
                "name": key,
                "scope": "system_config",
                "label": key,
                "fields": list(fields) or ["*"],
                "description": (row.description if row else "") or "",
                "configured": configured,
                "encrypted": status in ("encrypted", "empty"),
                "plaintext": status == "plaintext",
                "status": status,
                "rotatable": rotatable,
                "change_entry": "" if rotatable else REPLACE_ONLY_SYSTEM_CONFIG_KEYS.get(key, ""),
                "used_by": str(SYSTEM_CONFIG_USAGE_HINTS.get(key, "")),
                "masked": MASK if configured else "",
                "updated_time": _timestamp(row.updated_time) if row else "",
                **_rotation_fields(rotatable=rotatable, configured=configured, last=rotation.get(key)),
            }
        )
    return rows


def _model_credential_rows(rotation: dict[str, Any]) -> list[Any]:
    """模型字段级凭据（值级加密，按「已配置数量」聚合，不暴露任何值）。"""
    rows = []
    for name, meta, model in _resolved_model_credentials():
        field = meta["field"]
        queryset = model.objects.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True})
        configured = queryset.count()
        latest = queryset.order_by("-updated_time").values_list("updated_time", flat=True).first()
        rows.append(
            {
                "name": name,
                "scope": "model_field",
                "label": str(meta["label"]),
                "description": "",
                "configured": configured > 0,
                "configured_count": configured,
                "encrypted": True,
                "plaintext": False,
                "status": "encrypted" if configured else "empty",
                "rotatable": bool(meta.get("rotatable")),
                "change_entry": meta.get("change_entry", ""),
                "used_by": str(MODEL_CREDENTIAL_USAGE_HINTS.get(name, "")),
                "masked": MASK if configured else "",
                "updated_time": _timestamp(latest),
                **_rotation_fields(
                    rotatable=bool(meta.get("rotatable")),
                    configured=configured > 0,
                    last=rotation.get(name),
                ),
            }
        )
    return rows


# 实现拆至 system.services.credential_rotate：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = (
    "_model_secret_plaintext",
    "regenerate_system_config",
    "rotate_model_field",
    "rotate_setting",
    "rotate_system_config",
)


def __getattr__(name: Any) -> Any:
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("system.services.credential_rotate"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
