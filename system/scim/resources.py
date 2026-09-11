# -*- coding: utf-8 -*-
"""SCIM 资源映射与读写（RFC 7644 子集：Users / Groups 核心操作）。

设计约束：
- **用户名策略与后台一致**：唯一性按 `UserInfo.all_objects`（含回收站占用）校验，
  避免"后台改不了的名字被 SCIM 建出来"；
- **凭据不下发**：payload 无 password 时置不可用密码（登录走 OAuth2/OIDC 联邦）；
- **停用即失效**：active=false / DELETE 复用 `force_logout_user`（令牌失效时间戳 +
  refresh 拉黑 + WS 踢线 + 会话置离线），与在线用户管理同一套链路；
- **组 → 角色**：Group 映射 `UserRole`（displayName → name，externalId → code，
  缺省 code 由 displayName 规范化生成），成员即 `UserInfo.roles` 关系；
- **审计**：所有写操作落 OperationLog（auth_type=scim，changes 记字段级 diff，
  绝不落 password 原文）。
"""

import json
import re
import uuid

from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

#: SCIM 协议常量（RFC 7643/7644）
SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_SERVICE_PROVIDER = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"

LIST_COUNT_DEFAULT = 100
LIST_COUNT_MAX = 500
GROUP_CODE_PATTERN = re.compile(r"[^0-9a-zA-Z_]+")


class ScimApiError(Exception):
    """SCIM 协议错误：视图统一渲染为 RFC 7644 §3.12 的 Error 文档。"""

    def __init__(self, status: int, detail: str, scim_type: str = ""):
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        super().__init__(detail)


def _iso(value) -> str:
    """SCIM 时间格式：UTC ISO8601，秒级 + Z（Django 默认 isoformat 的 +00:00 不兼容部分 IdP）。"""
    from datetime import timezone as dt_timezone

    if not value:
        return ""
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _meta(resource_type: str, obj, path: str) -> dict:
    return {
        "resourceType": resource_type,
        "created": _iso(getattr(obj, "created_time", None)),
        "lastModified": _iso(getattr(obj, "updated_time", None)),
        "location": f"{path}/{obj.pk}",
    }


def _first_value(values, key="value"):
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict) and item.get(key):
                return str(item[key]).strip()
    return ""


def user_resource(user, base_path: str = "/api/scim/v2/Users") -> dict:
    """User 资源序列化（只读投影：不含密码等敏感字段）。"""
    display = user.nickname or user.username
    payload = {
        "schemas": [SCHEMA_USER],
        "id": str(user.pk),
        "userName": user.username,
        "name": {"formatted": display},
        "displayName": display,
        "active": bool(user.is_active),
        "emails": [{"value": user.email, "primary": True}] if user.email else [],
        "phoneNumbers": [{"value": user.phone, "primary": True}] if user.phone else [],
        "meta": _meta("User", user, base_path),
    }
    roles = list(user.roles.values_list("code", flat=True))
    if roles:
        payload["groups"] = [{"value": code, "display": code} for code in roles]
    return payload


def group_resource(role, base_path: str = "/api/scim/v2/Groups") -> dict:
    """Group 资源序列化：成员 = 拥有该角色的用户。"""
    from system.models import UserInfo

    members = UserInfo.objects.filter(roles=role, is_active=True).order_by("pk")
    payload = {
        "schemas": [SCHEMA_GROUP],
        "id": str(role.pk),
        "displayName": role.name,
        "externalId": role.code,
        "members": [
            {"value": str(user.pk), "display": user.username, "$ref": f"/api/scim/v2/Users/{user.pk}"}
            for user in members
        ],
        "meta": _meta("Group", role, base_path),
    }
    return payload


def list_response(resources: list, total: int, start_index: int) -> dict:
    return {
        "schemas": [SCHEMA_LIST],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def error_response(status: int, detail: str, scim_type: str = "") -> dict:
    payload = {"schemas": [SCHEMA_ERROR], "detail": detail, "status": str(status)}
    if scim_type:
        payload["scimType"] = scim_type
    return payload


def parse_filter(filter_value: str) -> tuple[str, str]:
    """解析 `attr eq "value"`（RFC 7644 子集）；不支持的操作符 / 属性返回 400。"""
    if not filter_value:
        return "", ""
    match = re.match(r'^\s*([A-Za-z]+)\s+eq\s+"(.*)"\s*$', filter_value)
    if not match:
        raise ScimApiError(400, str(_("Unsupported filter expression")), scim_type="invalidFilter")
    attribute, value = match.group(1), match.group(2)
    if attribute not in ("userName", "externalId", "displayName", "id"):
        raise ScimApiError(400, str(_("Unsupported filter attribute")), scim_type="invalidFilter")
    return attribute, value


def parse_paging(query_params) -> tuple[int, int]:
    try:
        start_index = int(query_params.get("startIndex") or 1)
        count = int(query_params.get("count") if query_params.get("count") is not None else LIST_COUNT_DEFAULT)
    except (TypeError, ValueError):
        raise ScimApiError(400, str(_("Invalid pagination parameters")), scim_type="invalidValue") from None
    start_index = max(start_index, 1)
    count = min(max(count, 0), LIST_COUNT_MAX)
    return start_index, count


# ---------------------------------------------------------------- Users


def _resolve_username(payload: dict) -> str:
    username = str(payload.get("userName") or "").strip()
    if not username:
        email = _first_value(payload.get("emails"))
        username = email.split("@")[0] if email else ""
    if not username:
        raise ScimApiError(400, str(_("userName is required")), scim_type="invalidValue")
    return username[:150]


def _display_name(payload: dict, fallback: str) -> str:
    name = payload.get("displayName")
    if not name and isinstance(payload.get("name"), dict):
        name = payload["name"].get("formatted")
    return str(name or fallback).strip()[:150]


def _apply_payload(user, payload: dict, *, creating: bool) -> list:
    """把 SCIM payload 写入用户对象（不 save），返回变更字段列表。"""
    from system.models import UserInfo

    changed = []
    username = str(payload.get("userName") or "").strip()
    if username and username != user.username:
        if UserInfo.all_objects.filter(username=username).exclude(pk=user.pk).exists():
            raise ScimApiError(409, str(_("userName already exists")), scim_type="uniqueness")
        user.username = username[:150]
        changed.append("username")

    if creating or "displayName" in payload or isinstance(payload.get("name"), dict):
        display = _display_name(payload, user.nickname or user.username)
        if display != (user.nickname or ""):
            user.nickname = display
            changed.append("nickname")

    if creating or "emails" in payload:
        email = _first_value(payload.get("emails"))
        if email != (user.email or ""):
            user.email = email[:254]
            changed.append("email")

    if creating or "phoneNumbers" in payload:
        phone = _first_value(payload.get("phoneNumbers"))
        if phone != (user.phone or ""):
            user.phone = phone[:16]
            changed.append("phone")

    if "active" in payload:
        active = bool(payload.get("active"))
        if active != bool(user.is_active):
            user.is_active = active
            changed.append("is_active")
    return changed


def create_user(payload: dict):
    """创建用户：无 password 时置不可用密码（只能经身份联邦登录）。"""
    from common.core.config import SysConfig
    from system.models import UserInfo, UserRole

    username = _resolve_username(payload)
    if UserInfo.all_objects.filter(username=username).exists():
        raise ScimApiError(409, str(_("userName already exists")), scim_type="uniqueness")

    user = UserInfo(username=username, is_active=True)
    _apply_payload(user, payload, creating=True)
    password = payload.get("password")
    if password:
        user.set_password(str(password))
    else:
        user.set_unusable_password()
    user.save()

    role_code = str(SysConfig.SCIM_DEFAULT_ROLE_CODE or "").strip()
    if role_code:
        role = UserRole.objects.filter(code=role_code, is_active=True).first()
        if role:
            user.roles.add(role)
        else:
            logger.warning("SCIM default role not found: %s", role_code)
    return user


def update_user(user, payload: dict) -> list:
    """PUT：整体替换（未提供字段按 SCIM 语义置空/默认；username 不可空）。"""
    from system.models import UserInfo

    if "userName" not in payload:
        raise ScimApiError(400, str(_("userName is required")), scim_type="invalidValue")
    username = str(payload.get("userName") or "").strip()
    if not username:
        raise ScimApiError(400, str(_("userName is required")), scim_type="invalidValue")
    if UserInfo.all_objects.filter(username=username).exclude(pk=user.pk).exists():
        raise ScimApiError(409, str(_("userName already exists")), scim_type="uniqueness")

    user.username = username[:150]
    user.nickname = _display_name(payload, username)
    user.email = _first_value(payload.get("emails"))[:254]
    user.phone = _first_value(payload.get("phoneNumbers"))[:16]
    user.is_active = bool(payload.get("active", True))
    user.save()
    return ["username", "nickname", "email", "phone", "is_active"]


def patch_user(user, operations: list) -> list:
    """PATCH：支持 replace/add active/displayName/name.formatted/emails/phoneNumbers/userName。

    remove 操作按"置空"处理（SCIM 的 remove 语义为删除属性）。
    """
    from system.models import UserInfo

    changed: list = []
    for operation in operations or []:
        if not isinstance(operation, dict):
            raise ScimApiError(400, str(_("Invalid patch operation")), scim_type="invalidValue")
        op = str(operation.get("op") or "").lower()
        path = str(operation.get("path") or "").strip()
        value = operation.get("value")
        if op not in ("add", "replace", "remove"):
            raise ScimApiError(400, str(_("Unsupported patch op: {}").format(op)), scim_type="invalidSyntax")
        if op == "remove":
            value = "" if path in ("displayName", "name.formatted") else None
        # 无 path 的 value 为属性包（RFC 7644 §3.5.2）
        if not path and isinstance(value, dict):
            for key, item in value.items():
                changed += _apply_patch_path(user, key, item)
            continue
        changed += _apply_patch_path(user, path, value)

    if "username" in changed:
        if not user.username or UserInfo.all_objects.filter(username=user.username).exclude(pk=user.pk).exists():
            raise ScimApiError(409, str(_("userName already exists")), scim_type="uniqueness")
    user.save()
    return sorted(set(changed))


def _apply_patch_path(user, path: str, value) -> list:
    if path in ("active", "urn:ietf:params:scim:schemas:core:2.0:User:active"):
        user.is_active = bool(value)
        return ["is_active"]
    if path in ("displayName", "name.formatted", "name"):
        if isinstance(value, dict):
            value = value.get("formatted") or value.get("displayName")
        user.nickname = str(value or "")[:150]
        return ["nickname"]
    if path == "userName":
        user.username = str(value or "").strip()[:150] or user.username
        return ["username"]
    if path == "emails":
        user.email = _first_value(value if isinstance(value, list) else [value])[:254]
        return ["email"]
    if path == "phoneNumbers":
        user.phone = _first_value(value if isinstance(value, list) else [value])[:16]
        return ["phone"]
    logger.info("SCIM patch: unsupported path ignored: %s", path)
    return []


def deactivate_user(user, operator: str = "scim") -> int:
    """停用用户并踢掉全部会话（与在线用户强制下线同一链路）。"""
    from system.utils.session import force_logout_user

    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active", "updated_time"])
    return force_logout_user(user.pk, operator=operator)


# ---------------------------------------------------------------- Groups


def _group_code(payload: dict) -> str:
    code = str(payload.get("externalId") or "").strip()
    if not code:
        code = GROUP_CODE_PATTERN.sub("_", str(payload.get("displayName") or "").strip()).strip("_").lower()
    if not code:
        code = f"scim_group_{uuid.uuid4().hex[:8]}"
    return code[:128]


def create_group(payload: dict):
    from system.models import UserRole

    display_name = str(payload.get("displayName") or "").strip()
    if not display_name:
        raise ScimApiError(400, str(_("displayName is required")), scim_type="invalidValue")
    code = _group_code(payload)
    if UserRole.objects.filter(code=code).exists():
        raise ScimApiError(409, str(_("Group code already exists")), scim_type="uniqueness")
    role = UserRole.objects.create(name=display_name[:128], code=code, is_active=True)
    _sync_group_members(role, payload.get("members"), replace=True)
    return role


def update_group(role, payload: dict) -> None:
    display_name = str(payload.get("displayName") or "").strip()
    if display_name:
        role.name = display_name[:128]
    code = payload.get("externalId")
    if code:
        from system.models import UserRole

        code = str(code).strip()[:128]
        if UserRole.objects.filter(code=code).exclude(pk=role.pk).exists():
            raise ScimApiError(409, str(_("Group code already exists")), scim_type="uniqueness")
        role.code = code
    role.save()
    _sync_group_members(role, payload.get("members"), replace=True)


def patch_group(role, operations: list) -> None:
    from system.models import UserRole

    for operation in operations or []:
        if not isinstance(operation, dict):
            raise ScimApiError(400, str(_("Invalid patch operation")), scim_type="invalidValue")
        op = str(operation.get("op") or "").lower()
        path = str(operation.get("path") or "").strip()
        value = operation.get("value")
        if op in ("add", "replace") and path in ("", "members"):
            members = value if isinstance(value, list) else (value or {}).get("members")
            # add = 追加成员；replace = 整体替换（RFC 7644 §3.5.2）
            _sync_group_members(role, members, replace=op == "replace")
        elif op == "remove" and path.startswith("members"):
            # members[value eq "<pk>"] 形式（RFC 7644 §3.5.2.2）或 value 列表
            target = None
            match = re.search(r'value\s+eq\s+"([^"]+)"', path)
            if match:
                target = [{"value": match.group(1)}]
            elif isinstance(value, list):
                target = value
            _remove_group_members(role, target or [])
        elif op in ("add", "replace") and path == "displayName":
            role.name = str(value or "")[:128]
            role.save()
        elif op in ("add", "replace") and path == "externalId":
            code = str(value or "").strip()[:128]
            if code and UserRole.objects.filter(code=code).exclude(pk=role.pk).exists():
                raise ScimApiError(409, str(_("Group code already exists")), scim_type="uniqueness")
            role.code = code or role.code
            role.save()
        else:
            logger.info("SCIM group patch: unsupported operation ignored: %s %s", op, path)


def _sync_group_members(role, members, *, replace: bool) -> None:
    from system.models import UserInfo

    if replace:
        role_users = list(UserInfo.objects.filter(roles=role))
        for user in role_users:
            user.roles.remove(role)
    for member in members or []:
        if not isinstance(member, dict):
            continue
        user = _resolve_member(member)
        if user is not None:
            user.roles.add(role)


def _remove_group_members(role, members) -> None:
    for member in members or []:
        if not isinstance(member, dict):
            continue
        user = _resolve_member(member)
        if user is not None:
            user.roles.remove(role)


def _resolve_member(member: dict):
    """成员解析：`value` 支持主键（int/uuid）或 username（IdP 常用 externalId 直传）。"""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from system.models import UserInfo

    value = str(member.get("value") or "").strip()
    if not value:
        return None
    queryset = UserInfo.all_objects
    user = None
    try:
        user = queryset.filter(pk=value).first()
    except (ValueError, TypeError, DjangoValidationError):
        user = None
    if user is None:
        user = queryset.filter(username=value).first()
    if user is None:
        raise ScimApiError(400, str(_("Group member not found: {}").format(value)), scim_type="invalidValue")
    return user


def delete_group(role) -> None:
    role.delete()


# ---------------------------------------------------------------- 审计


def write_audit(request, *, action: str, object_pk: str = "", changes: dict | None = None, status_code: int = 1000):
    """SCIM 操作审计：落 OperationLog（auth_type=scim），changes 记字段级 diff。

    刻意不记录请求体（可能含 password / 敏感属性）；审计失败不影响业务响应。
    """
    from common.utils.request import get_request_ip
    from system.models import OperationLog

    try:
        OperationLog.objects.create(
            module=f"SCIM:{action}",
            path=(request.path or "")[:400],
            method=(request.method or "")[:8],
            object_pk=str(object_pk or "")[:64],
            ipaddress=get_request_ip(request),
            status_code=status_code,
            response_code=status_code,
            auth_type=OperationLog.AuthType.SCIM,
            changes=json.dumps(changes, ensure_ascii=False, default=str)[:4096] if changes else None,
        )
    except Exception:  # noqa: BLE001 审计链路故障不影响同步结果
        logger.warning("write scim audit failed", exc_info=True)
