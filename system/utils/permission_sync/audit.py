#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：审计报告。"""

import re

from .constants import AUDIT_KNOWN_DUPLICATES, AUDIT_SKIP_PREFIXES


def audit_permission_menus(routes, perms):
    """报告：未匹配任何路由的权限点 / 重复的 (path, method)。

    匹配用「样例化为真实请求路径」的路由地址（正则原文含 `(?P<pk>...)`，
    不能直接做字符串/正则比较）。
    """
    unmatched, duplicates, exempted, known_duplicates = [], [], [], []
    seen = {}
    for perm in perms:
        key = (perm.path, (perm.method or "").upper())
        if key in seen:
            if key in AUDIT_KNOWN_DUPLICATES:
                known_duplicates.append(perm)
            else:
                duplicates.append(perm)
        else:
            seen[key] = perm

        if perm.path.startswith(AUDIT_SKIP_PREFIXES):
            exempted.append(perm)
            continue

        method = (perm.method or "").upper()
        matched = False
        for route in routes:
            if method and method not in {item.upper() for item in route.actions}:
                continue
            sample = route.sample
            if perm.path in (sample.lstrip("/"), f"{sample.lstrip('/')}$"):
                matched = True
                break
            try:
                if re.match("/" + perm.path, sample) or re.match("/" + perm.path, f"{sample}/"):
                    matched = True
                    break
            except re.error:
                continue
        if not matched:
            unmatched.append(perm)
    return unmatched, duplicates, exempted, known_duplicates
