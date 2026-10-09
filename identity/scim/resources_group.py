# -*- coding: utf-8 -*-
"""SCIM Groups 资源读写（自 system/scim/resources.py 平移）。

对外的既有导入面（identity.scim.resources）经该模块再导出保持不变。
"""

import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from identity.scim.errors import ScimApiError
from identity.scim.guards import ensure_group_writable

logger = get_logger(__name__)

#: 群组 code 规范化：非字母数字下划线替换为下划线
GROUP_CODE_PATTERN = re.compile(r"[^0-9a-zA-Z_]+")


# ---------------------------------------------------------------- Groups


def _group_code(payload: dict[str, Any]) -> str:
    code = str(payload.get("externalId") or "").strip()
    if not code:
        code = GROUP_CODE_PATTERN.sub("_", str(payload.get("displayName") or "").strip()).strip("_").lower()
    if not code:
        code = f"scim_group_{uuid.uuid4().hex[:8]}"
    return code[:128]


def create_group(payload: dict[str, Any]) -> Any:
    from identity.models import UserRole

    display_name = str(payload.get("displayName") or "").strip()
    if not display_name:
        raise ScimApiError(400, str(_("displayName is required")), scim_type="invalidValue")
    code = _group_code(payload)
    if UserRole.objects.filter(code=code).exists():
        raise ScimApiError(409, str(_("Group code already exists")), scim_type="uniqueness")
    role = UserRole.objects.create(name=display_name[:128], code=code, is_active=True)
    _sync_group_members(role, payload.get("members"), replace=True)
    return role


def update_group(role: Any, payload: dict[str, Any]) -> None:
    ensure_group_writable(role)
    display_name = str(payload.get("displayName") or "").strip()
    if display_name:
        role.name = display_name[:128]
    code = payload.get("externalId")
    if code:
        from identity.models import UserRole

        code = str(code).strip()[:128]
        if UserRole.objects.filter(code=code).exclude(pk=role.pk).exists():
            raise ScimApiError(409, str(_("Group code already exists")), scim_type="uniqueness")
        role.code = code
    role.save()
    _sync_group_members(role, payload.get("members"), replace=True)


def patch_group(role: Any, operations: list[Any]) -> None:
    from identity.models import UserRole

    ensure_group_writable(role)
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


def _sync_group_members(role: Any, members: Any, *, replace: bool) -> None:
    from identity.models import UserInfo

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


def _remove_group_members(role: Any, members: Any) -> None:
    for member in members or []:
        if not isinstance(member, dict):
            continue
        user = _resolve_member(member)
        if user is not None:
            user.roles.remove(role)


def _resolve_member(member: dict[str, Any]) -> Any:
    """成员解析：`value` 支持主键（int/uuid）或 username（IdP 常用 externalId 直传）。"""

    from identity.models import UserInfo

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


def delete_group(role: Any) -> None:
    ensure_group_writable(role)
    role.delete()
