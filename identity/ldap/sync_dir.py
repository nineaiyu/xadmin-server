#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 组织架合同步：部门树 / 用户绑定 / 缺失策略（自 sync.py 拆分，行为不变）。"""

import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.services import OperationLog
from common.utils import get_logger
from identity.ldap.client import (
    entry_to_attrs,
    first_attr,
    get_attr_map,
    is_entry_disabled,
    normalize_dn,
    paged_search_entries,
)
from identity.ldap.sync import (
    DEPT_CODE_PREFIX,
    LDAP_FILTER_OU,
    _sync_roles,
)
from identity.models import DeptInfo, LdapUserBinding, UserInfo

logger = get_logger(__name__)


# ---------------------------------------------------------------- 部门


def _norm_code(dn: str) -> str:
    return DEPT_CODE_PREFIX + dn


def _sync_depts(conn, summary: dict) -> dict:
    """同步 OU 为部门树。返回 {规范化父 DN: dept_pk} 供用户归属计算。"""
    if not settings.LDAP_DEPT_ENABLED or not settings.LDAP_DEPT_SEARCH_BASE:
        return {}
    entries = paged_search_entries(conn, settings.LDAP_DEPT_SEARCH_BASE, LDAP_FILTER_OU, ["ou", "name"])
    by_dn = {}
    for entry in entries:
        attrs = entry_to_attrs(entry)
        name = first_attr(attrs, "name") or first_attr(attrs, "ou")
        if name:
            by_dn[normalize_dn(entry["dn"])] = str(name)[:128]

    # 层级排序保证父部门先落库（目录为空时跳过写入，但仍执行下方消失清扫）
    for dn in sorted(by_dn, key=lambda item: item.count(",")):
        name = by_dn[dn]
        parent_dn = dn.split(",", 1)[1] if "," in dn else ""
        parent = None
        if parent_dn and parent_dn in by_dn:
            parent = DeptInfo.objects.filter(code=_norm_code(parent_dn)).only("pk").first()
        try:
            with transaction.atomic():
                dept, created = DeptInfo.objects.get_or_create(
                    code=_norm_code(dn)[:128],
                    defaults={"name": name, "parent": parent, "is_active": True},
                )
                if created:
                    summary["created_depts"] += 1
                elif dept.name != name or dept.parent_id != (parent.pk if parent else None):
                    dept.name = name
                    dept.parent = parent
                    if not dept.is_active:
                        dept.is_active = True
                    dept.save()
                    summary["updated_depts"] += 1
        except Exception:  # noqa: BLE001 单部门失败不中断整批
            logger.warning("LDAP sync dept failed: %s", dn, exc_info=True)

    # 目录侧消失的 ldap:* 部门：非 ignore 策略时停用（不做软删，保留树结构）
    seen_codes = [_norm_code(dn)[:128] for dn in by_dn]
    if settings.LDAP_SYNC_MISSING_POLICY != "ignore":
        missing = DeptInfo.objects.filter(code__startswith=DEPT_CODE_PREFIX, is_active=True).exclude(
            code__in=seen_codes
        )
        summary["deactivated_depts"] += missing.update(is_active=False)
    DeptInfo.invalid_dept_tree_cache()
    return {dn: _norm_code(dn)[:128] for dn in by_dn}


def _resolve_user_dept(user_dn: str, dept_by_dn: dict):
    """按用户 DN 的最近祖先 OU 归属部门；无匹配祖先返回 None。"""
    dn = user_dn
    while "," in dn:
        dn = dn.split(",", 1)[1]
        code = dept_by_dn.get(dn)
        if code:
            return DeptInfo.objects.filter(code=code).only("pk").first()
    return None


# ---------------------------------------------------------------- 用户


def _extract_fields(attrs: dict, attr_map: dict) -> dict:
    fields = {}
    for key, default_attr in (("nickname", "cn"), ("email", "mail"), ("phone", "telephoneNumber")):
        value = first_attr(attrs, attr_map.get(key, default_attr))
        if value is not None and str(value).strip():
            fields[key] = str(value).strip()
    return fields


def _audit_conflict(dn: str, username: str, reasons: list):
    try:
        OperationLog.objects.create(
            module="LDAP:conflict",
            object_pk=str(username or "")[:64],
            auth_type=OperationLog.AuthType.LDAP,
            status_code=1001,
            response_code=1001,
            changes=json.dumps({"dn": dn, "username": username, "reasons": reasons}, ensure_ascii=False)[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响同步
        logger.warning("write LDAP conflict audit failed", exc_info=True)


def _sync_users(conn, summary: dict, dept_by_dn: dict):
    attr_map = get_attr_map()
    username_attr = attr_map.get("username", "sAMAccountName")
    group_attr = getattr(settings, "LDAP_ATTR_GROUPS", "memberOf")
    attributes = sorted(set(attr_map.values()) | {"userAccountControl", group_attr})
    entries = paged_search_entries(conn, settings.LDAP_USER_SEARCH_BASE, settings.LDAP_USER_FILTER, attributes)
    summary["total_entries"] = len(entries)
    seen_dns = set()

    for entry in entries:
        dn = normalize_dn(entry["dn"])
        seen_dns.add(dn)
        attrs = entry_to_attrs(entry)
        username = first_attr(attrs, username_attr)
        username = str(username).strip() if username else ""
        if not username:
            summary["skipped_users"] += 1
            continue
        try:
            with transaction.atomic():
                _sync_one_user(dn, username, attrs, attr_map, dept_by_dn, summary)
        except Exception:  # noqa: BLE001 单条目失败不中断整批（savepoint 已回滚）
            summary["skipped_users"] += 1
            logger.warning("LDAP sync user failed: %s", dn, exc_info=True)

    _apply_missing_policy(seen_dns, summary)


def _sync_one_user(dn, username, attrs, attr_map, dept_by_dn, summary):
    disabled = is_entry_disabled(attrs)
    fields = _extract_fields(attrs, attr_map)
    binding = LdapUserBinding.objects.select_related("user").filter(dn=dn).first()

    if binding is not None:
        user = binding.user
        if user is None:  # 绑定悬空（不该发生，CASCADE 保证），按新建处理
            binding.delete()
        else:
            _update_user(user, binding, dn, fields, disabled, dept_by_dn, summary)
            _sync_roles(user, attrs, summary)
            return

    local = UserInfo.all_objects.filter(username__iexact=username).first()
    if local is not None:
        # 用户名被本地账号或回收站占用：跳过 + 冲突审计（不冒名更新他人账号）
        summary["conflict_users"] += 1
        reason = "username occupied by recycled user" if local.deleted_at else "username occupied by local user"
        _audit_conflict(dn, username, [reason])
        return
    if not settings.LDAP_SYNC_AUTO_CREATE:
        summary["skipped_users"] += 1
        return

    email = fields.get("email", "")
    phone = fields.get("phone", "")
    reasons = []
    if email and UserInfo.all_objects.filter(email=email).exists():
        reasons.append(f"email conflict cleared: {email}")
        email = ""
    if phone and UserInfo.all_objects.filter(phone=phone).exists():
        reasons.append(f"phone conflict cleared: {phone}")
        phone = ""
    dept = _resolve_user_dept(dn, dept_by_dn) if dept_by_dn else None
    user = UserInfo.objects.create_user(
        username=username,
        nickname=fields.get("nickname", username),
        email=email,
        phone=phone[:16],
    )
    user.is_active = not disabled
    user.dept = dept
    user.save(update_fields=["is_active", "dept"])
    LdapUserBinding.objects.create(user=user, dn=dn, synced_at=timezone.now())
    summary["created_users"] += 1
    if reasons:
        _audit_conflict(dn, username, reasons)
    _sync_roles(user, attrs, summary)


def _update_user(user, binding, dn, fields, disabled, dept_by_dn, summary):
    changed = []
    if user.deleted_at is not None:
        # 目录重新出现：自动恢复（回收站 restore），绑定关系保留
        user.deleted_at = None
        user.is_active = True
        changed.extend(["deleted_at", "is_active"])
        summary["restored_users"] += 1
    if not user.is_active and not disabled:
        user.is_active = True
        changed.append("is_active")
    if disabled and user.is_active:
        user.is_active = False
        changed.append("is_active")
    for field in ("nickname", "email", "phone"):
        value = fields.get(field)
        if value is None:
            continue
        if field in ("email", "phone"):
            occupied = UserInfo.all_objects.filter(**{field: value}).exclude(pk=user.pk).exists()
            if occupied:
                summary["conflict_users"] += 1
                _audit_conflict(dn, user.username, [f"{field} conflict cleared: {value}"])
                value = ""
        if str(getattr(user, field) or "") != value:
            setattr(user, field, value)
            changed.append(field)
    if dept_by_dn:
        dept = _resolve_user_dept(dn, dept_by_dn)
        if dept and dept.pk != user.dept_id:
            user.dept = dept
            changed.append("dept")
    if changed:
        user.save()
        summary["updated_users"] += 1
    binding.synced_at = timezone.now()
    binding.save(update_fields=["synced_at", "updated_time"])


def _apply_missing_policy(seen_dns: set, summary: dict):
    """目录侧已消失的绑定用户按策略处置（ignore/deactivate/soft_delete）。"""
    policy = settings.LDAP_SYNC_MISSING_POLICY
    if policy == "ignore":
        return
    stale = (
        LdapUserBinding.objects.select_related("user").exclude(dn__in=seen_dns).filter(user__deleted_at__isnull=True)
    )
    for binding in stale.iterator():
        user = binding.user
        if user is None:
            continue
        try:
            with transaction.atomic():
                if policy == "soft_delete":
                    user.delete()  # SoftDeleteModel：进回收站可恢复
                    summary["soft_deleted_users"] += 1
                else:
                    if user.is_active:
                        user.is_active = False
                        user.save()
                        summary["deactivated_users"] += 1
                    binding.synced_at = timezone.now()
                    binding.save(update_fields=["synced_at", "updated_time"])
        except Exception:  # noqa: BLE001 单条失败不中断整批
            logger.warning("LDAP missing policy failed: %s", binding.dn, exc_info=True)
