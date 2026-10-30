#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""跨域切分的数据回灌定义：旧库（``system_*`` 表名）↔ 新库（``identity`` / ``file`` / ``audit`` 分域）。

四域切分同时改了表名与列集，旧库无法通过迁移收敛（见 ``migration_ledger.PRE_SPLIT_TABLES``），
官方升级路径是「备份 → 清库重建 → 回灌」。本模块提供与实现无关的中间格式：

- ``ENTITIES``：要搬运的实体、导出侧/导入侧模型、自然键（跨版本稳定）、标量字段、关联字段；
- ``serialize_value`` / ``deserialize_value``：JSON 友好的值转换（UUID / 时间 / Decimal / bytes）；
- ``entity_names`` / ``build_payload``：导出包的结构与校验。

两个命令共用本模块：``legacy_export`` 在旧库执行（只读旧模型），``legacy_import`` 在新库执行
（只写新模型）。关联一律用**自然键**而非主键（部门/角色/数据权限/菜单在两侧都稳定：``code`` /
``name``），因此旧库的 UUID 不需要保留。
"""

from __future__ import annotations

import base64
import datetime
import decimal
import uuid
from typing import Any

FORMAT = "xadmin-legacy-export"
FORMAT_VERSION = 1
FILES_DIR = "files"

# 实体定义：
#   export_model / import_model —— 导出侧（旧代码）与导入侧（新代码）各自的模型，惰性解析；
#   key —— 自然键字段（幂等 upsert 依据）；
#   fields —— 直接搬运的标量字段（两侧同名）；
#   fk —— {本侧字段: ("目标实体", "目标字段")}，按自然键解析；
#   m2m —— {本侧字段: ("目标实体", 目标自然键字段)}，按自然键集合解析。
ENTITIES: dict[str, dict[str, Any]] = {
    "depts": {
        "export_model": ("system", "DeptInfo"),
        "import_model": ("identity", "DeptInfo"),
        "key": "code",
        "fields": ("name", "code", "rank", "is_active", "auto_bind"),
        "fk": {"parent": ("depts", "code")},
        "m2m": {"roles": ("roles", "code"), "rules": ("data_permissions", "name")},
    },
    "roles": {
        "export_model": ("system", "UserRole"),
        "import_model": ("identity", "UserRole"),
        "key": "code",
        "fields": ("name", "code", "is_active"),
        "m2m": {"menu": ("menus", "name")},
    },
    "users": {
        "export_model": ("system", "UserInfo"),
        "import_model": ("identity", "UserInfo"),
        "key": "username",
        "fields": (
            "username",
            "password",
            "nickname",
            "gender",
            "email",
            "phone",
            "first_name",
            "last_name",
            "is_active",
            "is_superuser",
            "date_joined",
            "last_login",
        ),
        "fk": {"dept": ("depts", "code")},
        "m2m": {"roles": ("roles", "code"), "rules": ("data_permissions", "name")},
        "file_fields": ("avatar",),
    },
    "data_permissions": {
        "export_model": ("system", "DataPermission"),
        "import_model": ("system", "DataPermission"),
        "key": "name",
        "fields": ("name", "rules", "is_active"),
        "m2m": {"menu": ("menus", "name")},
    },
    "system_configs": {
        "export_model": ("system", "SystemConfig"),
        "import_model": ("system", "SystemConfig"),
        "key": "key",
        "fields": ("key", "value", "is_active", "access", "inherit"),
    },
    "personal_configs": {
        "export_model": ("system", "UserPersonalConfig"),
        "import_model": ("system", "UserPersonalConfig"),
        "key": "signature",
        "fields": ("key", "value", "is_active", "access"),
        "fk": {"owner": ("users", "username")},
    },
    "settings": {
        "export_model": ("settings", "Setting"),
        "import_model": ("settings", "Setting"),
        "key": "name",
        "fields": ("name", "value", "category", "encrypted", "is_active"),
    },
    "field_permissions": {
        "export_model": ("system", "FieldPermission"),
        "import_model": ("system", "FieldPermission"),
        "key": "signature",
        "fields": (),
        "fk": {"role": ("roles", "code"), "menu": ("menus", "name")},
        # 字段权限引用模型字段树：按「模型/字段名 + 类型」复合键匹配（新库的字段树由迁移自动生成）
        "m2m": {"field": ("model_fields", ("name", "field_type"))},
    },
    "user_msg_subscriptions": {
        "export_model": ("notifications", "UserMsgSubscription"),
        "import_model": ("notifications", "UserMsgSubscription"),
        "key": "signature",
        "fields": ("message_type", "receive_backends"),
        "fk": {"user": ("users", "username")},
    },
    "system_msg_subscriptions": {
        "export_model": ("notifications", "SystemMsgSubscription"),
        "import_model": ("notifications", "SystemMsgSubscription"),
        "key": "message_type",
        "fields": ("message_type", "receive_backends"),
        "m2m": {"users": ("users", "username")},
    },
    "files": {
        "export_model": ("system", "UploadFile"),
        "import_model": ("file", "UploadFile"),
        "key": "signature",
        "fields": ("filename", "filesize", "mime_type", "is_upload", "is_tmp", "file_url"),
        "storage_field": "filepath",
    },
    "notices": {
        "export_model": ("notifications", "MessageContent"),
        "import_model": ("notifications", "MessageContent"),
        "key": "signature",
        "fields": ("title", "message", "level", "notice_type", "publish", "created_time"),
        # notice_user 是 through=MessageUserRead 的多对多：目标用户与已读状态同源，
        # 统一由 reads 重建（直接 set 会与 through 表冲突）
        "m2m": {
            "notice_dept": ("depts", "code"),
            "notice_role": ("roles", "code"),
            "file": ("files", "filename"),
        },
        "reads": {"model": ("notifications", "MessageUserRead"), "owner": "username", "flag": "unread"},
    },
}

# 关联目标实体（menus 由种子提供，只做解析、不搬运）
REFERENCE_ENTITIES: dict[str, dict[str, Any]] = {
    "menus": {"import_model": ("system", "Menu"), "key": "name"},
    # 字段权限引用的模型字段树：由迁移/同步命令在新库自动生成，只做匹配、不搬运
    "model_fields": {"import_model": ("system", "ModelLabelField"), "key": ("name", "field_type")},
}


def normalize_keys(key: Any) -> tuple[str, ...]:
    """把自然键规范成元组形态（单键与复合键统一处理）。"""
    if isinstance(key, (tuple, list)):
        return tuple(str(item) for item in key)
    return (str(key),)


def join_key(values: list[Any]) -> str:
    """复合自然键的连接口径（导出与导入两侧一致）。"""
    return "|".join("" if item is None else str(item) for item in values)


def entity_names() -> list[str]:
    """可搬运实体名（供命令的 --include 校验）。"""
    return sorted(ENTITIES)


def parse_include(raw: str | None) -> list[str]:
    """解析 --include 参数；缺省为全部实体。未知实体直接报错（避免静默漏搬）。"""
    if not raw:
        return entity_names()
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in names if item not in ENTITIES]
    if unknown:
        raise ValueError(f"未知实体：{', '.join(unknown)}；可选：{', '.join(entity_names())}")
    return names


def serialize_value(value: Any) -> Any:
    """把数据库值转成 JSON 可写的形态（不含外键对象与文件内容）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    return str(value)


def deserialize_value(value: Any) -> Any:
    """还原 serialize_value 的转换（时间保持字符串，由 Django 字段自行解析）。"""
    if isinstance(value, dict) and set(value.keys()) == {"__bytes__"}:
        return base64.b64decode(value["__bytes__"])
    return value


# 需要同时搬运物理文件的用户字段（头像等）
USER_FILE_FIELDS: tuple[str, ...] = ("avatar",)


def personal_config_signature(owner: str, key: str) -> str:
    """用户个人配置的自然键：所属用户 + 配置键。"""
    return f"{owner}:{key}"


def file_signature(filename: str, filesize: int | None) -> str:
    """附件与公告的自然键：文件名 + 大小（旧库无唯一约束，用组合键做幂等依据）。"""
    return f"{filename}:{filesize or 0}"


def notice_signature(title: str, created_time: Any) -> str:
    """公告自然键：标题 + 创建时间（同标题可多次发布）。"""
    return f"{title}:{created_time or ''}"


def build_payload(source_version: str, data: dict[str, Any]) -> dict[str, Any]:
    """构造导出包（命令写入 JSON 的顶层结构）。"""
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "source_version": source_version,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "counts": {name: len(rows) for name, rows in data.items()},
        "data": data,
    }


def validate_payload(payload: dict[str, Any]) -> None:
    """校验导出包格式（供导入侧提前失败，而不是写到一半报错）。"""
    if payload.get("format") != FORMAT:
        raise ValueError(f"不是本工具导出的数据包（format={payload.get('format')!r}）")
    if int(payload.get("format_version", 0)) > FORMAT_VERSION:
        raise ValueError(f"导出包版本 {payload.get('format_version')} 高于当前工具支持的 {FORMAT_VERSION}，请升级工具")
    if not isinstance(payload.get("data"), dict):
        raise ValueError("导出包缺少 data 段")
