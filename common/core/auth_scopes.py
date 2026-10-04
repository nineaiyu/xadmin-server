#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""访问令牌（PAT / API 应用）的 scope 匹配与 IP 白名单判定（自 auth.py 拆分，行为不变）。

scope 条目形如 ``GET /api/system/user``（方法名可选，缺省仅匹配路径）；path 占位与
菜单权限点 path 正则同口径。"""

import functools
import ipaddress
import re

from common.utils import get_logger

logger = get_logger(__name__)


# scope 条目可选的方法前缀：`GET /api/system/user`（方法名 + 空白 + 路径）
SCOPE_METHOD_RE = re.compile(r"^(?P<method>[A-Za-z]{3,7})\s+(?P<path>\S.*)$")
SCOPE_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def split_scope_entry(pattern) -> tuple:
    """拆分 scope 条目为 ``(method, path)``；无方法前缀时 method 为 None。

    仅当首段是合法 HTTP 方法名时才按「方法 + 路径」解析，避免把含空格的
    路径/正则条目误判（历史条目一律按纯路径口径）。
    """
    text = str(pattern).strip()
    match = SCOPE_METHOD_RE.match(text)
    if match and match.group("method").upper() in SCOPE_HTTP_METHODS:
        return match.group("method").upper(), match.group("path").strip()
    return None, text


def _normalize_scope_path(path) -> str:
    """scope 条目的路径部分 → 锚定正则串（空返回空串；正则非法抛 ``ValueError``）。

    - ``^…$`` 完整锚定：原样保留（作者可控的自定义正则）；
    - 其余（路径 / 前缀 / 单侧锚点）：去锚点后统一包裹——``^(?:core)(/.*)?$``
      放行该路径及其子路径（``/api/system/user`` 命中 ``/api/system/user/1``），
      但不粘连同前缀兄弟地址（不再命中 ``/api/system/user-logs``）；
    - 以 ``$`` 结尾表示精确语义 → ``^(?:core)$``（仅该地址本身）。
    """
    body = str(path or "").strip()
    if not body:
        return ""
    if body.startswith("^") and body.endswith("$"):
        anchored = body
    else:
        exact = body.endswith("$")
        core = body.lstrip("^")
        if exact:
            core = core[:-1]
        core = core.rstrip("/")
        if not core:
            return ""
        if not core.startswith("/"):
            core = f"/{core}"
        anchored = f"^(?:{core})$" if exact else f"^(?:{core})(/.*)?$"
    try:
        re.compile(anchored)
    except re.error as exc:
        raise ValueError(str(exc)) from exc
    return anchored


def normalize_scope_entry(pattern) -> str:
    """scope 条目规范化：统一为**锚定**形态（保存时收口 + 运行期兜底同源）。

    形态约定与 ``system/utils/identity/pat_scope.py::scope_entry`` 的输出一致，可安全重复规范化；
    ``METHOD /path`` 条目的方法前缀原样保留（仅路径部分锚定）。空条目 / 规范化后为空
    返回空串（调用方跳过）；正则非法抛 ``ValueError``，写入侧转成校验错误。
    """
    text = str(pattern or "").strip()
    if not text:
        return ""
    method, path = split_scope_entry(text)
    anchored = _normalize_scope_path(path)
    if not anchored:
        return ""
    return f"{method} {anchored}" if method else anchored


@functools.lru_cache(maxsize=4096)
def _compiled_scope_matcher(path_part: str):
    """路径部分 → 编译后正则（进程内缓存）；不可规范化/正则非法返回 None。"""
    try:
        normalized = _normalize_scope_path(path_part)
        return re.compile(normalized) if normalized else None
    except ValueError:
        return None


def path_allowed_by_scopes(path: str, scopes, method: str | None = None) -> bool:
    """PAT scope 判定：空清单 = 不限（既有 token 向后兼容）。

    条目语义（大小写不敏感，与 SENSITIVE_OPERATION_PATHS 同口径）：

    - ``METHOD /path``：仅该 HTTP 方法放行（如 ``GET /api/system/user``）；
    - 纯路径/正则：不限方法。

    匹配前统一经 :func:`normalize_scope_entry` 锚定（历史库内可能存有手写的非锚定
    条目，如 ``api/system/user``——锚定后不再粘连命中 ``/api/system/user-logs``）；
    已锚定条目原样使用。非法正则跳过并告警，不 500、不放任整清单失效；方法限定
    条目在请求方法未知时不放行（fail-closed）。
    """
    if not scopes:
        return True
    for pattern in scopes:
        if not pattern:
            continue
        entry_method, entry_path = split_scope_entry(pattern)
        if entry_method and entry_method != (method or "").upper():
            continue
        matcher = _compiled_scope_matcher(entry_path)
        if matcher is None:
            logger.warning("pat scope skipped: invalid or empty path regex %s", entry_path)
            continue
        if matcher.search(path) is not None:
            return True
    return False


def ip_allowed_by_allowlist(client_ip: str, allowlist) -> bool:
    """PAT IP 白名单判定：空清单 = 不限；支持单个 IP 与 CIDR 网段。

    fail-closed：无法解析的客户端 IP 视为不匹配；非法条目跳过并告警
    （与 scope 非法正则同口径：不 500、也不放任整清单失效）。
    """
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address(str(client_ip))
    except ValueError:
        logger.warning("pat ip not parseable: %s", client_ip)
        return False
    for entry in allowlist:
        text = str(entry or "").strip()
        if not text:
            continue
        try:
            if "/" in text:
                if addr in ipaddress.ip_network(text, strict=False):
                    return True
            elif addr == ipaddress.ip_address(text):
                return True
        except ValueError:
            logger.warning("pat ip allowlist skipped: invalid entry %s", text)
            continue
    return False
