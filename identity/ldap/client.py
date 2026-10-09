#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 连接与搜索的轻封装。

只依赖 ldap3 与 django.conf.settings；不吞异常——「目录不可达」与「密码错误」
由调用方按异常类型/结果区分。连接对象可整体替换（测试注入 fake 连接）。

连接函数默认读 django settings（``config=None`` → ``LdapConfig.from_settings``）；
显式传 ``LdapConfig`` 快照时完全按快照连搜（管理页「测试连接」按表单值
构造快照传参，不临时改写进程全局 settings）。
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from common.utils import get_logger

logger = get_logger(__name__)

__all__ = [
    "LDAPException",
    "LdapConfig",
    "LdapConfigError",
    "build_server",
    "service_connection",
    "user_connection",
    "paged_search_entries",
    "entry_to_attrs",
    "escape_filter",
    "get_attr_map",
    "normalize_dn",
]

# AD userAccountControl 禁用位（ACCOUNTDISABLE）
AD_UAC_DISABLED_BIT = 0x2


class LdapConfigError(Exception):
    """LDAP 配置不完整（如未填 SERVER_URI / SEARCH_BASE），区别于连接失败。"""


# settings 键名 -> 快照字段名（from_values 消费；测试连接的表单快照同键名）
_SETTING_KEY_TO_FIELD = {
    "LDAP_SERVER_URI": "server_uri",
    "LDAP_START_TLS": "start_tls",
    "LDAP_BIND_DN": "bind_dn",
    "LDAP_BIND_PASSWORD": "bind_password",
    "LDAP_CONNECT_TIMEOUT": "connect_timeout",
    "LDAP_USER_SEARCH_BASE": "user_search_base",
    "LDAP_USER_FILTER": "user_filter",
    "LDAP_ATTR_USERNAME": "attr_username",
    "LDAP_ATTR_NICKNAME": "attr_nickname",
    "LDAP_ATTR_EMAIL": "attr_email",
    "LDAP_ATTR_PHONE": "attr_phone",
    "LDAP_DEPT_ENABLED": "dept_enabled",
    "LDAP_DEPT_SEARCH_BASE": "dept_search_base",
    "LDAP_SYNC_PAGE_SIZE": "sync_page_size",
}


@dataclass(frozen=True)
class LdapConfig:
    """LDAP 运行配置快照：显式传参形态（缺省值与 server/conf 默认值表对齐）。"""

    server_uri: str = ""
    start_tls: bool = False
    bind_dn: str = ""
    bind_password: str = ""
    connect_timeout: int = 10
    user_search_base: str = ""
    user_filter: str = "(objectClass=person)"
    attr_username: str = "sAMAccountName"
    attr_nickname: str = "cn"
    attr_email: str = "mail"
    attr_phone: str = "telephoneNumber"
    dept_enabled: bool = False
    dept_search_base: str = ""
    sync_page_size: int = 500

    @classmethod
    def from_settings(cls) -> "LdapConfig":
        """读 django settings 当前生效值（登录/同步链路的默认形态）。"""
        return cls(
            server_uri=settings.LDAP_SERVER_URI or "",
            start_tls=bool(settings.LDAP_START_TLS),
            bind_dn=settings.LDAP_BIND_DN or "",
            bind_password=settings.LDAP_BIND_PASSWORD or "",
            connect_timeout=settings.LDAP_CONNECT_TIMEOUT,
            user_search_base=settings.LDAP_USER_SEARCH_BASE or "",
            user_filter=settings.LDAP_USER_FILTER or "(objectClass=person)",
            attr_username=getattr(settings, "LDAP_ATTR_USERNAME", "sAMAccountName"),
            attr_nickname=getattr(settings, "LDAP_ATTR_NICKNAME", "cn"),
            attr_email=getattr(settings, "LDAP_ATTR_EMAIL", "mail"),
            attr_phone=getattr(settings, "LDAP_ATTR_PHONE", "telephoneNumber"),
            dept_enabled=bool(getattr(settings, "LDAP_DEPT_ENABLED", False)),
            dept_search_base=getattr(settings, "LDAP_DEPT_SEARCH_BASE", "") or "",
            sync_page_size=settings.LDAP_SYNC_PAGE_SIZE,
        )

    @classmethod
    def from_values(cls, values: dict[str, Any]) -> "LdapConfig":
        """从 settings 键名 dict 构造快照（测试连接：表单值 ∪ 已存配置）。"""
        kwargs = {field: values[key] for key, field in _SETTING_KEY_TO_FIELD.items() if values.get(key) is not None}
        return cls(**kwargs)


def _resolve(config: "LdapConfig | None") -> LdapConfig:
    return config if config is not None else LdapConfig.from_settings()


def build_server(config: "LdapConfig | None" = None) -> Any:
    cfg = _resolve(config)
    uri = cfg.server_uri
    if not uri:
        raise LdapConfigError("LDAP_SERVER_URI is empty")
    return Server(
        uri,
        use_ssl=uri.lower().startswith("ldaps://"),
        connect_timeout=cfg.connect_timeout,
        get_info=ALL,
    )


def service_connection(config: "LdapConfig | None" = None) -> Any:
    """服务账号连接（按快照 bind_dn/bind_password）；bind 失败抛 LDAPException。"""
    cfg = _resolve(config)
    server = build_server(cfg)
    conn = Connection(
        server,
        user=cfg.bind_dn or None,
        password=cfg.bind_password or None,
        auto_bind=True,
        read_only=True,
    )
    if cfg.start_tls:
        conn.start_tls()
    return conn


def user_connection(user_dn: Any, password: Any, config: "LdapConfig | None" = None) -> Any:
    """以用户 DN + 密码 bind（认证判定）；auto_bind 失败抛 LDAPException。"""
    server = build_server(config)
    conn = Connection(server, user=user_dn, password=password, auto_bind=True, read_only=True)
    if _resolve(config).start_tls:
        conn.start_tls()
    return conn


def paged_search_entries(
    conn: Any, search_base: Any, search_filter: Any, attributes: Any, config: "LdapConfig | None" = None
) -> Any:
    """分页搜索，返回条目属性 dict 列表（每条含 dn 与 attributes）。"""
    if not search_base:
        raise LdapConfigError("LDAP search base is empty")
    entries = conn.extend.standard.paged_search(
        search_base=search_base,
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=attributes,
        paged_size=_resolve(config).sync_page_size,
        generator=False,
    )
    return [entry for entry in entries if entry.get("type") == "searchResEntry"]


def entry_to_attrs(entry: Any) -> dict[str, Any]:
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


def first_attr(attrs: dict[str, Any], key: str) -> Any:
    """取单值：列表属性取首项；空返回 None。key 先精确匹配再退回小写。"""
    value = attrs.get(key, attrs.get((key or "").lower()))
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def is_entry_disabled(attrs: dict[str, Any]) -> bool:
    """AD userAccountControl 禁用位判定；无该属性（OpenLDAP 等）视为启用。"""
    uac = first_attr(attrs, "userAccountControl")
    try:
        return bool(int(uac) & AD_UAC_DISABLED_BIT)
    except (TypeError, ValueError):
        return False


def escape_filter(value: str) -> str:
    typed_value: str = escape_filter_chars(value or "")
    return typed_value


def get_attr_map(config: "LdapConfig | None" = None) -> dict[str, Any]:
    """字段映射（固定四键）：平台字段 -> 目录属性名（LDAP_ATTR_* 可在管理页改）。"""
    cfg = _resolve(config)
    return {
        "username": cfg.attr_username,
        "nickname": cfg.attr_nickname,
        "email": cfg.attr_email,
        "phone": cfg.attr_phone,
    }


def normalize_dn(dn: str) -> str:
    """DN 规范化：小写 + 去 RDN 间空格，作为绑定/同步两侧一致的目录身份键。

    简易处理转义逗号（``\\,``）；带转义逗号的极端 RDN 不在支持范围（登记边界）。
    """
    import re

    parts = re.split(r"(?<!\\),", (dn or "").strip())
    return ",".join(part.strip() for part in parts if part.strip()).lower()
