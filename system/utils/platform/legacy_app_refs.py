#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""二开 app 的迁移引用改写（异地 app 在 5.x 上重新可用）。

背景：四域切分改了 ``app_label`` 与表名，二开 app 的迁移里若是写死了旧引用
（``to="system.UserInfo"``、``dependencies=[("system", "0002_operationlog_...")]``），
在新库上 ``migrate`` 会因为「节点不存在 / 模型解析不到」直接失败——这也是「回灌会丢二开」
的机制性根源。本模块按**结构改名推导出的标签映射**（与 ``legacy_refs`` 同源）改写这些引用：

- ``to="system.UserInfo"`` → ``to="identity.UserInfo"``（FK/OneToOne 目标）；
- ``dependencies`` 里指向已消失节点的元组 → 该 app 当前的初始迁移（如
  ``("system", "0002_operationlog_exec_time_...")`` → ``("system", "0001_initial")``）；
- FK 目标换了 app 时，补一条对该 app 初始迁移的依赖（否则迁移图顺序不保证）。

**fail-closed**：映射不到旧模型（客户自己的定制表/字段）一律不猜，作为 ``unresolved``
列出来交人工；改写只做文本级替换，改完由用户自己跑 ``makemigrations --check`` 复核。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

# to="app.Model" / to='app.Model'
_FK_REF_RE = re.compile(r'(to\s*=\s*)(["\'])([a-z_][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\2')
# ("app", "0002_node") / ('app', '0002_node')
_DEP_RE = re.compile(r'\((["\'"])([a-z_][a-z0-9_]*)\1,\s*(["\'"])([^"\']+)\3\)')
_DEPENDENCIES_START_RE = re.compile(r"^\s*dependencies\s*=\s*\[", re.M)


def current_initial(app: str, loader: Any = None) -> str | None:
    """该 app 在当前代码链里的初始迁移名（用于替换已消失的依赖节点）。"""
    from django.db.migrations.loader import MigrationLoader

    loader = loader or MigrationLoader(None, ignore_no_migrations=False)
    names = sorted(name for (label, name) in loader.disk_migrations if label == app and name.startswith("0001"))
    return names[0] if names else None


def label_moves_from_tag(tag: str) -> dict[str, str]:
    """离线推导旧标签 → 新标签：按「模型名在当前 app registry 里唯一」匹配。

    不需要连库：历史 tag 的迁移目录给出旧 ``(app, model)``，当前 app registry 给出模型现在归属的
    app；同名模型唯一才认（有歧义的不猜）。这正是二开迁移改写要用的映射。
    """
    from django.apps import apps

    from system.utils.platform import legacy_schema

    old_models = legacy_schema.load_tag_models(tag)
    current: dict[str, list[str]] = {}
    for model in apps.get_models(include_auto_created=False, include_swapped=True):
        current.setdefault(model._meta.object_name.lower(), []).append(model._meta.app_label)
    moves: dict[str, str] = {}
    for _table, meta in old_models.items():
        key = meta["model"].lower()
        candidates = current.get(key, [])
        if len(candidates) == 1 and candidates[0] != meta["app"]:
            moves[f"{meta['app']}.{key}"] = f"{candidates[0]}.{key}"
    return moves


def _rewrite_fk(line: str, moves: dict[str, str], findings: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix, quote, app, model = match.groups()
        old_label = f"{app}.{model.lower()}"
        new_label = moves.get(old_label)
        if new_label is None:
            findings["unresolved"].append(f"{old_label}（第 {match.start()} 列附近，迁移引用里的外键目标）")
            return match.group(0)
        new_app, _, new_model = new_label.partition(".")
        findings["fk_targets"].append(f"{old_label} → {new_label}")
        findings["needed_apps"].add(new_app)
        return f"{prefix}{quote}{new_app}.{model}{quote}"

    return _FK_REF_RE.sub(replace, line)


def _rewrite_dependency(line: str, findings: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        quote, app, quote2, node = match.groups()
        if app in _MANAGED_LABELS and not node.startswith("0001"):
            initial = current_initial(app)
            if initial and initial != node:
                findings["dependencies"].append(f"({app}, {node}) → ({app}, {initial})")
                return f"({quote}{app}{quote}, {quote2}{initial}{quote2})"
        findings["needed_apps"].add(app)
        return match.group(0)

    return _DEP_RE.sub(replace, line)


_MANAGED_LABELS = ("system", "common", "notifications", "settings", "captcha")


def scan_app(app_path: Path, moves: dict[str, str] | None = None) -> dict[str, Any]:
    """扫描一个二开 app 的迁移文件（只读）：给出将被改写的位置与未解决项。"""
    moves = moves or {}
    findings: dict[str, Any] = {
        "app": str(app_path),
        "files": [],
        "fk_targets": [],
        "dependencies": [],
        "apps_to_depend": [],
        "unresolved": [],
        "needed_apps": set(),
    }
    migrations = sorted((app_path / "migrations").glob("*.py"))
    for path in migrations:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "to=" in line:
                _rewrite_fk(line, moves, findings)
            if re.search(r'^\s*(\(|")', line):
                _rewrite_dependency(line, findings)
        findings["files"].append(str(path))
    findings["apps_to_depend"] = sorted(findings["needed_apps"])
    findings.pop("needed_apps", None)
    return findings


def rewrite_app(app_path: Path, moves: dict[str, str], dry_run: bool = True) -> dict[str, Any]:
    """改写二开 app 的迁移引用；``dry_run`` 只产出 diff。"""
    result: dict[str, Any] = {"dry_run": dry_run, "files": [], "unresolved": [], "diff": []}
    for path in sorted((app_path / "migrations").glob("*.py")):
        if path.name == "__init__.py":
            continue
        original = path.read_text(encoding="utf-8")
        findings: dict[str, Any] = {
            "unresolved": [],
            "fk_targets": [],
            "dependencies": [],
            "needed_apps": set(),
        }
        updated = "\n".join(
            _rewrite_dependency(_rewrite_fk(line, moves, findings) if "to=" in line else line, findings)
            for line in original.splitlines()
        )
        updated = _ensure_app_dependencies(updated, findings)
        if findings["unresolved"]:
            result["unresolved"].extend(f"{path.name}: {item}" for item in findings["unresolved"])
        if updated != original:
            diff = list(
                difflib.unified_diff(
                    original.splitlines(),
                    updated.splitlines(),
                    fromfile=f"a/{path.name}",
                    tofile=f"b/{path.name}",
                    lineterm="",
                )
            )
            result["diff"].extend(diff)
            result["files"].append(
                {
                    "file": str(path),
                    "fk_targets": sorted(set(findings["fk_targets"])),
                    "dependencies": sorted(set(findings["dependencies"])),
                }
            )
            if not dry_run:
                path.write_text(updated, encoding="utf-8")
    return result


def _ensure_app_dependencies(text: str, findings: dict[str, Any]) -> str:
    """规整 ``dependencies`` 块并补上 FK 目标所在 app 的依赖。

    不做字符串拼接而是**重建**该块（列表元素与缩进统一重建）：拼接容易漏逗号/丢缩进，
    重建后第二次运行必然产出同样文本（幂等）。
    """
    if not findings.get("needed_apps"):
        return text
    match = re.search(r"(?P<indent>[ \t]*)dependencies\s*=\s*\[(?P<body>.*?)\]", text, re.S)
    if not match:
        return text
    entries = re.findall(r"\(\s*[\"\']([a-z_][a-z0-9_]*)[\"\']\s*,\s*[\"\']([^\"\']+)[\"\']\s*\)", match.group("body"))
    existing = {app for app, _node in entries}
    additions = [(app, "0001_initial") for app in sorted(findings["needed_apps"]) if app not in existing]
    rendered = [f'("{app}", "{node}")' for app, node in entries + additions]
    indent = match.group("indent") + "    "
    base = match.group("indent")
    block = f"{base}dependencies = [\n" + "".join(f"{indent}{item},\n" for item in rendered) + f"{base}]"
    findings["dependencies"].extend(f"新增依赖 ({app}, 0001_initial)" for app, _node in additions)
    return text[: match.start()] + block + text[match.end() :]
