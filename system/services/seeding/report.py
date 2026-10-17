#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子计划的命令行输出：``--list`` 的装载地图与 ``--check`` 的问题清单。"""

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from django.conf import settings

from .types import LEVEL_ERROR, PLATFORM, Problem, SeedFileSpec, SeedPlan

_LAYER_WIDTH = 8


def count_rows(spec: SeedFileSpec) -> int:
    """读取种子文件的行数；不可读时返回 -1（由 ``--check`` 负责报告具体错误）。"""

    try:
        rows = json.loads(spec.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return -1
    return len(rows) if isinstance(rows, list) else -1


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path(settings.PROJECT_DIR)))
    except ValueError:
        return str(path)


def _layer_label(spec: SeedFileSpec) -> str:
    return "平台层" if spec.app == PLATFORM else spec.app


def format_plan(plan: SeedPlan, *, with_sample: bool = True) -> str:
    """输出装载地图：层 / 文件 / 模型 / 行数 / 类别，顺序即装载顺序。"""

    specs = [*plan.all_specs(), *(plan.samples if with_sample else ())]
    lines = [
        f"装载计划：平台层 {len(plan.platform)} 个文件，应用层 {len(plan.apps)} 个应用"
        + (f"（{'、'.join(plan.apps)}）" if plan.apps else ""),
    ]
    for spec in specs:
        rows = count_rows(spec)
        rows_text = "读取失败" if rows < 0 else f"{rows} 行"
        lines.append(
            f"  {_layer_label(spec):<{_LAYER_WIDTH}} {_display_path(spec.path):<52} {spec.model:<30} {rows_text:>8}  {spec.kind}"
        )
    if plan.samples and not with_sample:
        lines.append(f"  （另有 {len(plan.samples)} 个示例种子文件，加 --with-sample 后装载）")
    for note in plan.notes:
        lines.append(f"  说明：{note}")
    return "\n".join(lines)


def format_problems(problems: Sequence[Problem]) -> str:
    """输出校验结果；无问题时给出明确结论。"""

    if not problems:
        return "种子校验通过：0 个问题。"
    errors = sum(1 for problem in problems if problem.level == LEVEL_ERROR)
    warnings = len(problems) - errors
    if not errors:
        lines = [f"种子校验通过（0 error / {warnings} warning）："]
    else:
        lines = [f"种子校验未通过：{errors} 个 error / {warnings} 个 warning"]
    lines.extend(f"  {problem}" for problem in problems)
    return "\n".join(lines)


def format_ownership(summary: Any) -> str:
    """输出归属分布表（按应用 × 种子类别），并列出未判定项。"""

    if summary is None:
        return ""
    totals = summary.totals
    lines = [
        f"归属分布（菜单 {totals['menu']} / 字段树 {totals['field']} / 字段白名单 {totals['grant']}"
        f" / 配置 {totals['config']} / 字典 {totals['dict']}）：",
        f"  {'应用':<14}{'菜单':>6}{'字段树':>8}{'白名单':>8}{'配置':>6}{'字典':>6}",
    ]
    for app in summary.ordered_apps():
        row = summary.by_app[app]
        label = "平台层" if app == PLATFORM else app
        lines.append(
            f"  {label:<14}{row.get('menu', 0):>6}{row.get('field', 0):>8}{row.get('grant', 0):>8}"
            f"{row.get('config', 0):>6}{row.get('dict', 0):>6}"
        )
    if summary.mixed_pages:
        lines.append(f"  混合归属页面（保留平台层）：{'、'.join(summary.mixed_pages)}")
    if summary.undeclared_apps:
        lines.append(f"  未声明清单的应用（按平台层）：{'、'.join(summary.undeclared_apps)}")
    return "\n".join(lines)


def has_error(problems: Iterable[Problem]) -> bool:
    return any(problem.level == LEVEL_ERROR for problem in problems)
