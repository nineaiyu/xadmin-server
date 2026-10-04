#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 定时同步服务：OU → 部门树、目录条目 → 用户/状态。

设计要点：
- ``run_ldap_sync`` 为唯一入口，返回摘要 dict；连接/配置错误直接抛出
  （周期任务层转 TaskExecution FAILURE），不影响在线登录；
- 逐条目 savepoint 隔离，单条失败不中断整批；
- 冲突（用户名被本地账号/回收站占用等）默认跳过并逐条 ``OperationLog``
  审计（module=LDAP:conflict），摘要另行落一条 LDAP:sync 审计；
- 目录侧消失的用户按 ``LDAP_SYNC_MISSING_POLICY``（deactivate/soft_delete/
  ignore）处置；目录重新出现时自动恢复（软删→restore、禁用→启用）；
- 部门树 code 以 ``ldap:`` 前缀命名空间隔离，杜绝与手工部门编码冲突。
"""

import json

from django.conf import settings

from common.utils import get_logger
from system.ldap.client import (
    LdapConfigError,
    get_attr_map,
    paged_search_entries,
    service_connection,
)
from system.models import OperationLog

logger = get_logger(__name__)

DEPT_CODE_PREFIX = "ldap:"
LDAP_FILTER_OU = "(objectClass=organizationalUnit)"

# 摘要通知阈值以下的事件只在审计/日志留痕，避免每轮轰炸站内信
_SUMMARY_AUDIT_KEYS = [
    "created_users",
    "updated_users",
    "deactivated_users",
    "soft_deleted_users",
    "restored_users",
    "conflict_users",
    "created_depts",
    "updated_depts",
    "deactivated_depts",
    "skipped_users",
    "roles_added",
    "roles_removed",
    "total_entries",
]


def get_group_role_map() -> dict:
    """组 → 平台角色映射（LDAP_GROUP_ROLE_MAP；键为组 DN 或 CN，大小写不敏感）。"""
    mapping = getattr(settings, "LDAP_GROUP_ROLE_MAP", None) or {}
    result = {}
    for key, code in mapping.items():
        key, code = str(key).strip().lower(), str(code).strip()
        if key and code:
            result[key] = code
    return result


def _group_matches(key: str, values: list) -> bool:
    """组标识匹配：完整 DN 相等，或 DN 的 CN 段相等（`CN=<key>,...`）。"""
    for value in values:
        if value == key or value.startswith(f"cn={key},"):
            return True
    return False


def _sync_roles(user, attrs, summary) -> None:
    """按 LDAP 组映射挂/撤平台角色（只管理映射中出现的角色，不动手工授权）。

    - 命中组 → 挂对应角色；未命中 → 撤该角色；
    - 映射为空 = 不启用（存量行为零变化）；角色 code 不存在或已停用 → 跳过挂载。
    """
    mapping = get_group_role_map()
    if not mapping:
        return
    group_attr = getattr(settings, "LDAP_ATTR_GROUPS", "memberOf")
    raw = attrs.get(group_attr) or []
    values = [str(item).strip().lower() for item in (raw if isinstance(raw, (list, tuple)) else [raw])]
    wanted = {code for key, code in mapping.items() if _group_matches(key, values)}

    from system.models import UserRole

    mapped_codes = set(mapping.values())
    current = set(user.roles.filter(code__in=mapped_codes).values_list("code", flat=True))
    to_add, to_remove = wanted - current, current - wanted
    if to_add:
        roles = list(UserRole.objects.filter(code__in=to_add, is_active=True))
        if roles:
            user.roles.add(*roles)
            summary["roles_added"] += len(roles)
    if to_remove:
        user.roles.remove(*user.roles.filter(code__in=to_remove))
        summary["roles_removed"] += len(to_remove)


def run_ldap_sync() -> dict:
    from system.ldap.sync_dir import _sync_depts, _sync_users  # noqa: E402  (反向依赖，延迟导入避免循环)

    """执行一次完整同步。返回摘要 dict；连接失败抛 LDAPException/LdapConfigError。"""
    if not settings.LDAP_SYNC_ENABLED:
        return {"skipped": True, "reason": "LDAP_SYNC_ENABLED is off"}
    summary = {key: 0 for key in _SUMMARY_AUDIT_KEYS}
    with service_connection() as conn:
        dept_by_dn = _sync_depts(conn, summary)
        _sync_users(conn, summary, dept_by_dn)
    _audit_summary(summary)
    _notify_summary(summary)
    return summary


def test_ldap_connection(config=None) -> dict:
    """连接测试（管理页「测试」按钮）：服务 bind + 按配置快照实际搜索计数。

    ``config`` 传 ``LdapConfig`` 快照时完全按快照连搜（测试连接按表单值
    传参，不临时改写进程全局 settings）；传 None（缺省）读 django settings，
    与登录/同步链路同源。
    """
    from system.ldap.client import LdapConfig

    cfg = config if config is not None else LdapConfig.from_settings()
    if not cfg.server_uri:
        raise LdapConfigError("LDAP_SERVER_URI is empty")
    with service_connection(cfg) as conn:
        user_count = 0
        if cfg.user_search_base:
            user_count = len(
                paged_search_entries(
                    conn,
                    cfg.user_search_base,
                    cfg.user_filter,
                    [get_attr_map(cfg).get("username", "sAMAccountName")],
                    config=cfg,
                )
            )
        dept_count = 0
        if cfg.dept_enabled and cfg.dept_search_base:
            dept_count = len(paged_search_entries(conn, cfg.dept_search_base, LDAP_FILTER_OU, ["ou"], config=cfg))
    return {"user_count": user_count, "dept_count": dept_count}


# ---------------------------------------------------------------- 审计与通知


def _audit_summary(summary: dict):
    try:
        OperationLog.objects.create(
            module="LDAP:sync",
            auth_type=OperationLog.AuthType.LDAP,
            status_code=1000,
            response_code=1000,
            changes=json.dumps(summary, ensure_ascii=False)[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响同步结果
        logger.warning("write LDAP sync audit failed", exc_info=True)


def _notify_summary(summary: dict):
    """有处置/冲突动作时向超管发摘要通知（每轮最多一条）。"""
    interesting = {k: v for k, v in summary.items() if k not in ("total_entries",) and v}
    if not interesting:
        return
    try:
        from system.notifications import LdapSyncMessage

        LdapSyncMessage(interesting).publish()
    except Exception:  # noqa: BLE001 通知失败不影响同步结果
        logger.warning("send LDAP sync message failed", exc_info=True)


# 实现拆至 system.ldap.sync_dir：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = (
    "_apply_missing_policy",
    "_audit_conflict",
    "_extract_fields",
    "_norm_code",
    "_resolve_user_dept",
    "_sync_depts",
    "_sync_one_user",
    "_sync_users",
    "_update_user",
)


def __getattr__(name):
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("system.ldap.sync_dir"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
