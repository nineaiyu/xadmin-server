#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码挂载面覆盖门禁：匿名写端点必须过人机校验或显式豁免。

扫描 URLconf 中「匿名可达（authentication_classes / permission_classes 均为空）
且具有写方法」的端点，要求其视图源码出现统一入口（``require_human``）或兼容
薄封装（``check_captcha`` / ``check_token_and_captcha``）；否则失败。

豁免必须写明理由（本文件 EXEMPTIONS）：验证码端点自身、上游已发码的注册 /
重置 / 验证码登录、以及 MFA 自有因子流程。新增匿名写端点漏挂 = CI 红。
"""

import inspect
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django  # noqa: E402

django.setup()

from django.urls import get_resolver  # noqa: E402

WRITE_METHODS = ("post", "put", "patch", "delete")
COVERED_TOKENS = ("require_human", "check_token_and_captcha", "check_captcha")

EXEMPTIONS: tuple[tuple[str, str], ...] = (
    (r"api/identity/\^auth/captcha/(challenge|verify)\$", "人机校验端点自身（出题 / 校验）"),
    (r"api/identity/\^login/code\$", "验证码登录：人机校验在上游发码端点"),
    (r"api/identity/\^register\$", "注册：人机校验在上游发码端点"),
    (r"api/identity/\^auth/reset\$", "重置密码：人机校验在上游发码端点"),
    (r"api/identity/\^login/mfa/", "MFA 自有因子校验，不属于人机校验面"),
    (r"api/identity/\^auth/captcha/inspect\$", "E2E 专用答案检查端点（默认不挂载）"),
)


def iter_routes(patterns, prefix=""):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if hasattr(pattern, "url_patterns"):
            yield from iter_routes(pattern.url_patterns, route)
        else:
            yield route, pattern.callback


def is_exempt(route: str) -> bool:
    return any(re.search(pattern, route) for pattern, _reason in EXEMPTIONS)


def is_covered(view_class) -> bool:
    try:
        source = inspect.getsource(view_class)
    except (OSError, TypeError):
        return False
    return any(token in source for token in COVERED_TOKENS)


def anonymous_write_routes():
    seen = set()
    for route, callback in iter_routes(get_resolver().url_patterns):
        view_class = getattr(callback, "cls", None)
        if view_class is None:
            continue
        if getattr(view_class, "authentication_classes", None) != []:
            continue
        if getattr(view_class, "permission_classes", None) != []:
            continue
        methods = [method for method in WRITE_METHODS if hasattr(view_class, method)]
        if not methods:
            continue
        key = (route, tuple(methods))
        if key in seen:
            continue
        seen.add(key)
        yield route, methods, view_class


def main() -> int:
    violations = []
    covered = 0
    exempted = 0
    for route, methods, view_class in anonymous_write_routes():
        if is_exempt(route):
            exempted += 1
            continue
        if is_covered(view_class):
            covered += 1
            continue
        violations.append((route, ",".join(methods), view_class.__name__))
    for route, methods, name in violations:
        print(f"[captcha-coverage] 未挂人机校验：{methods} {route} ({name})")
    if violations:
        print(
            "匿名写端点必须调用 require_human / check_token_and_captcha，"
            "或在 scripts/check_captcha_coverage.py 的 EXEMPTIONS 写明理由"
        )
        return 1
    if covered == 0:
        print("[captcha-coverage] 未发现任何已覆盖的匿名写端点——扫描面可能失效")
        return 1
    print(f"验证码挂载面覆盖门禁通过（已覆盖 {covered} / 显式豁免 {exempted}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
