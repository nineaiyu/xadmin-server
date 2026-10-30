#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单标题静态门禁：约束「会进菜单显示名」的 docstring 首行长度。

背景：权限点的 ``MenuMeta.title`` 取自视图/动作 docstring 的**首行**
（``common.core.url_inventory.get_doc_first_line``）；首行过长会把菜单树与授权选择器
撑破。约定见 ``docs/architecture/handbook-backend.md``：首行是精炼的名词短语
（操作日志 module 与菜单显示名），详细说明下沉到后续段落。

口径：

- 视图类（``*ViewSet`` / ``*APIView``）的 docstring 首行 ≤ ``MENU_TITLE_MAX``；
- 视图类内 CRUD 方法（list/create/retrieve/update/partial_update/destroy）与
  ``@action`` 类装饰器方法的 docstring 首行 ≤ ``MENU_TITLE_MAX``；
- 无 docstring 的方法不检查（菜单回落到视图首行，已单独检查）；
- ``{cls}`` 占位符按原样计入长度（保守口径）。

阈值从 ``system/utils/platform/menu.py`` 的 ``MENU_TITLE_MAX`` 读取，避免双源漂移。

用法::

    python scripts/check_menu_titles.py            # 门禁（CI 用）
    python scripts/check_menu_titles.py --report   # 仅报告，恒不失败
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MENU_SOURCE = REPO_ROOT / "system" / "utils" / "platform" / "menu.py"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "docs",
    "tests",
    "migrations",
}

CRUD_METHODS = {"list", "create", "retrieve", "update", "partial_update", "destroy"}


def title_limit() -> int:
    match = re.search(r"^MENU_TITLE_MAX\s*=\s*(\d+)", MENU_SOURCE.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"无法从 {MENU_SOURCE} 读取 MENU_TITLE_MAX")
    return int(match.group(1))


def iter_python_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return files


def first_line(doc: str) -> str:
    return doc.strip().splitlines()[0].strip().rstrip("。") if doc and doc.strip() else ""


def is_view_class(node: ast.ClassDef) -> bool:
    return node.name.endswith("ViewSet") or node.name.endswith("APIView")


def is_action_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        name = target.id
    elif isinstance(target, ast.Attribute):
        name = target.attr
    else:
        return False
    return name == "action" or name.endswith("_action")


def check_file(path: Path, limit: int) -> list[tuple[str, int, int, str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    problems = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not is_view_class(node):
            continue
        doc = ast.get_docstring(node)
        if doc:
            line = first_line(doc)
            if len(line) > limit:
                problems.append((path.relative_to(REPO_ROOT).as_posix(), node.lineno, len(line), "class", line))

        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(item)
            if not doc:
                continue
            decorated = any(is_action_decorator(dec) for dec in item.decorator_list)
            if item.name not in CRUD_METHODS and not decorated:
                continue
            line = first_line(doc)
            if len(line) > limit:
                problems.append((path.relative_to(REPO_ROOT).as_posix(), item.lineno, len(line), item.name, line))
    return problems


def main() -> int:
    report_only = "--report" in sys.argv
    limit = title_limit()
    problems = []
    for path in iter_python_files():
        problems.extend(check_file(path, limit))

    if not problems:
        print(f"[menu-titles] 所有菜单标题来源 docstring 首行均 ≤ {limit} 字")
        return 0

    print(f"[menu-titles] 发现 {len(problems)} 处首行超过 {limit} 字（详细说明请下沉到后续段落）：")
    for path, lineno, length, name, line in problems:
        print(f"  {path}:{lineno}: {length} 字 | {name} | {line}")
    if report_only:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
