#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""声明式授权：``{角色 → 菜单名}`` 装载后并集应用。

``loaddata`` 对多对多是**覆盖式赋值**：同一个角色行被两个文件各装一次，后者会把前者的
授权清零。因此角色授权不能随种子文件一起拆，必须改成：

1. 各层用 ``grants.json`` 只声明「本层菜单被哪些角色拥有」（按 ``Menu.name`` 引用）；
2. 装载完成后统一做并集（``role.menu.add``），幂等、只增不删。

**语义变更（有意）**：改造前重灌种子会把角色菜单「覆盖」为种子列表，静默撤销管理员在
运行期额外授予的菜单；改为并集后种子只补它声明的部分，不再撤销其它授权。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .types import LEVEL_ERROR, LEVEL_WARNING, Problem, SeedFileSpec

GRANT_VERSION = 1


@dataclass(frozen=True)
class GrantDeclaration:
    """一层（平台层或某个 app）的授权声明。"""

    app: str
    path: Path
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class GrantReport:
    """授权应用结果：成功计数 + 缺失项（缺失不阻断，由调用方决定告警口径）。"""

    roles: int = 0
    menus: int = 0
    missing_roles: list[str] = field(default_factory=list)
    missing_menus: list[str] = field(default_factory=list)


def load_grants(specs: tuple[SeedFileSpec, ...]) -> tuple[list[GrantDeclaration], list[Problem]]:
    """读取计划内的全部 ``grants.json``。"""

    declarations: list[GrantDeclaration] = []
    problems: list[Problem] = []
    for spec in specs:
        declaration, issues = _load_one(spec)
        problems.extend(issues)
        if declaration is not None:
            declarations.append(declaration)
    return declarations, problems


def _load_one(spec: SeedFileSpec) -> tuple[GrantDeclaration | None, list[Problem]]:
    if not spec.path.is_file():
        return None, [Problem(LEVEL_ERROR, "GRANT_FILE_MISSING", "授权声明文件不存在", file=spec.path)]
    try:
        raw = json.loads(spec.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [Problem(LEVEL_ERROR, "GRANT_UNREADABLE", f"授权声明无法解析：{exc}", file=spec.path)]

    if not isinstance(raw, dict):
        return None, [Problem(LEVEL_ERROR, "GRANT_INVALID", "授权声明必须是 JSON 对象", file=spec.path)]
    if raw.get("version") != GRANT_VERSION:
        return None, [Problem(LEVEL_ERROR, "GRANT_INVALID", f"version 必须为 {GRANT_VERSION}", file=spec.path)]

    roles = raw.get("roles")
    if not isinstance(roles, dict):
        return None, [Problem(LEVEL_ERROR, "GRANT_INVALID", "roles 必须是对象（角色 → 菜单名数组）", file=spec.path)]

    problems: list[Problem] = []
    parsed: dict[str, tuple[str, ...]] = {}
    for code, items in roles.items():
        if not isinstance(code, str) or not code:
            problems.append(Problem(LEVEL_ERROR, "GRANT_INVALID", "角色编码必须是非空字符串", file=spec.path))
            continue
        if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
            problems.append(Problem(LEVEL_ERROR, "GRANT_INVALID", f"{code} 的菜单清单必须是字符串数组", file=spec.path))
            continue
        # 同一层内重复声明去重（跨层重复由并集自然合并）
        parsed[code] = tuple(dict.fromkeys(items))

    if not parsed:
        problems.append(Problem(LEVEL_WARNING, "GRANT_EMPTY", "授权声明为空", file=spec.path))
    return GrantDeclaration(app=spec.app, path=spec.path, roles=parsed), problems


def merge_grants(declarations: list[GrantDeclaration]) -> dict[str, tuple[str, ...]]:
    """把各层声明合并为「角色 → 菜单名（去重、稳定排序）」。"""

    merged: dict[str, set[str]] = {}
    for declaration in declarations:
        for code, items in declaration.roles.items():
            merged.setdefault(code, set()).update(items)
    return {code: tuple(sorted(items)) for code, items in merged.items()}


def validate_grants(
    declarations: list[GrantDeclaration],
    *,
    menu_names: set[str],
    role_codes: set[str],
) -> list[Problem]:
    """静态校验：声明的角色与菜单必须存在于本次装载的种子面内。"""

    problems: list[Problem] = []
    for declaration in declarations:
        for code, items in declaration.roles.items():
            if role_codes and code not in role_codes:
                problems.append(
                    Problem(
                        LEVEL_ERROR,
                        "GRANT_ROLE_UNKNOWN",
                        f"角色 {code} 不在种子角色清单内（{'、'.join(sorted(role_codes))}）",
                        file=declaration.path,
                    )
                )
            for name in items:
                if name not in menu_names:
                    problems.append(
                        Problem(
                            LEVEL_ERROR,
                            "GRANT_MENU_UNKNOWN",
                            f"角色 {code} 声明的菜单不存在：{name}",
                            file=declaration.path,
                        )
                    )
    return problems


def apply_grants(declarations: list[GrantDeclaration]) -> GrantReport:
    """装载后并集应用授权（幂等）。缺失的角色/菜单记入报告，由调用方告警。"""

    from identity.models import UserRole
    from system.models import Menu

    merged = merge_grants(declarations)
    report = GrantReport(roles=len(merged), menus=sum(len(items) for items in merged.values()))
    if not merged:
        return report

    wanted_names = {name for items in merged.values() for name in items}
    menus = list(Menu.objects.filter(name__in=wanted_names, deleted_at__isnull=True))
    by_name = {menu.name: menu for menu in menus}
    report.missing_menus = sorted(wanted_names - set(by_name))

    roles = list(UserRole.objects.filter(code__in=merged, deleted_at__isnull=True))
    by_code = {role.code: role for role in roles}
    report.missing_roles = sorted(set(merged) - set(by_code))

    for code, role in by_code.items():
        targets = [by_name[name] for name in merged[code] if name in by_name]
        if targets:
            role.menu.add(*targets)
    return report


def role_menu_names(code: str, declarations: list[GrantDeclaration]) -> tuple[str, ...]:
    """某个角色声明的菜单名（供演示卸载等需要「种子基准值」的场景复用）。"""

    return merge_grants(declarations).get(code, ())


def seed_role_menu_names(code: str) -> tuple[str, ...]:
    """不依赖数据库的便捷入口：按当前种子层读出某角色声明的菜单名。"""

    from .discover import discover
    from .plan import build_plan

    plan = build_plan(discover())
    declarations, _problems = load_grants(plan.grants)
    return role_menu_names(code, declarations)
