#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 连接与搜索的轻封装。

只依赖 ldap3 与 django.conf.settings；不吞异常——「目录不可达」与「密码错误」
由调用方按异常类型/结果区分。连接对象可整体替换（测试注入 fake 连接）。
"""

from django.conf import settings
from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from common.utils import get_logger

logger = get_logger(__name__)

__all__ = [
    "LDAPException",
    "LdapConfigError",
    "build_server",
    "service_connection",
    "user_connection",
    "paged_search_entries",
    "entry_to_attrs",
    "escape_filter",
]

# AD userAccountControl 禁用位（ACCOUNTDISABLE）
AD_UAC_DISABLED_BIT = 0x2


class LdapConfigError(Exception):
    """LDAP 配置不完整（如未填 SERVER_URI / SEARCH_BASE），区别于连接失败。"""


def build_server():
    uri = settings.LDAP_SERVER_URI
    if not uri:
        raise LdapConfigError("LDAP_SERVER_URI is empty")
    return Server(
        uri,
        use_ssl=uri.lower().startswith("ldaps://"),
        connect_timeout=settings.LDAP_CONNECT_TIMEOUT,
        get_info=ALL,
    )


def service_connection():
    """服务账号连接（读配置 bind_dn/bind_password）；bind 失败抛 LDAPException。"""
    server = build_server()
    conn = Connection(
        server,
        user=settings.LDAP_BIND_DN or None,
        password=settings.LDAP_BIND_PASSWORD or None,
        auto_bind=True,
        read_only=True,
    )
    if settings.LDAP_START_TLS:
        conn.start_tls()
    return conn


def user_connection(user_dn, password):
    """以用户 DN + 密码 bind（认证判定）；auto_bind 失败抛 LDAPException。"""
    server = build_server()
    conn = Connection(server, user=user_dn, password=password, auto_bind=True, read_only=True)
    if settings.LDAP_START_TLS:
        conn.start_tls()
    return conn


def paged_search_entries(conn, search_base, search_filter, attributes):
    """分页搜索，返回条目属性 dict 列表（每条含 dn 与 attributes）。"""
    if not search_base:
        raise LdapConfigError("LDAP search base is empty")
    entries = conn.extend.standard.paged_search(
        search_base=search_base,
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=attributes,
        paged_size=settings.LDAP_SYNC_PAGE_SIZE,
        generator=False,
    )
    return [entry for entry in entries if entry.get("type") == "searchResEntry"]


def entry_to_attrs(entry) -> dict:
    """ldap3 条目归一为 {attr: value|list}；多值属性取列表，单值取标量，None 视为缺失。"""
    raw = entry.get("attributes") or {}
    result = {}
    for key, value in raw.items():
        norm_key = key.lower()
        if isinstance(value, (list, tuple)):
            result[norm_key] = [v for v in value if v is not None]
        elif value is not None:
            result[norm_key] = value
    return result


def first_attr(attrs: dict, key: str):
    """取单值：列表属性取首项；空返回 None。key 先精确匹配再退回小写。"""
    value = attrs.get(key, attrs.get((key or "").lower()))
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def is_entry_disabled(attrs: dict) -> bool:
    """AD userAccountControl 禁用位判定；无该属性（OpenLDAP 等）视为启用。"""
    uac = first_attr(attrs, "userAccountControl")
    try:
        return bool(int(uac) & AD_UAC_DISABLED_BIT)
    except (TypeError, ValueError):
        return False


def escape_filter(value: str) -> str:
    return escape_filter_chars(value or "")


def get_attr_map() -> dict:
    """字段映射（固定四键）：平台字段 -> 目录属性名（LDAP_ATTR_* 可在管理页改）。"""
    return {
        "username": getattr(settings, "LDAP_ATTR_USERNAME", "sAMAccountName"),
        "nickname": getattr(settings, "LDAP_ATTR_NICKNAME", "cn"),
        "email": getattr(settings, "LDAP_ATTR_EMAIL", "mail"),
        "phone": getattr(settings, "LDAP_ATTR_PHONE", "telephoneNumber"),
    }


def normalize_dn(dn: str) -> str:
    """DN 规范化：小写 + 去 RDN 间空格，作为绑定/同步两侧一致的目录身份键。

    简易处理转义逗号（``\\,``）；带转义逗号的极端 RDN 不在支持范围（登记边界）。
    """
    import re

    parts = re.split(r"(?<!\\),", (dn or "").strip())
    return ",".join(part.strip() for part in parts if part.strip()).lower()
