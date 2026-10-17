#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子归属计算：每一行属于哪个 app（或平台共享层）。

归属不靠人工标注，全部由规则推导 + 清单声明共同决定：

- **权限点**：取 ``path``，按各 app 在 ``manifest.json`` 声明的 ``url_prefixes`` 做**最长前缀匹配**；
- **页面菜单**：取其权限点子节点的归属众数，单一应用占比 ≥ 80% 才判给该应用，否则留平台层
  （混合页面的父子引用跨应用，留在平台层最稳）；
- **目录菜单**：一律平台层（避免拆坏父引用）；
- **字段树**：向上找到根节点，根名 ``app.model`` 判给该 app（未声明清单的按平台层），
  ``*`` 与公共字段判给平台层；
- **字段白名单**：跟随其 ``menu`` 的归属；
- **配置键 / 字典**：按清单声明的 ``config_keys`` / ``dict_codes``，未声明的按平台层（渐进收口）。

判定结果只用于校验与迁移导航，不改变任何装载行为。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .types import LEVEL_ERROR, LEVEL_WARNING, PLATFORM, Problem, SeedManifest

MENU_MODEL = "system.menu"
FIELD_MODEL = "system.modellabelfield"
GRANT_MODEL = "system.fieldpermission"
CONFIG_MODEL = "system.systemconfig"
DICT_MODEL = "system.datadict"

MENU_TYPE_DIRECTORY = 0
MENU_TYPE_PAGE = 1
MENU_TYPE_PERMISSION = 2

# 单一应用占比达到该阈值才把页面判给该应用（否则留平台层）
PAGE_MAJORITY_RATIO = 0.8

# 三方模型所属 app：字段树归平台层，且不产出「未声明清单」提示
THIRD_PARTY_MODEL_APPS = ("django_celery_beat",)

FAMILIES = ("menu", "field", "grant", "config", "dict")


@dataclass(frozen=True)
class OwnershipSummary:
    """归属分布：按应用 × 种子类别计数，附未判定项清单。"""

    by_app: dict[str, dict[str, int]] = field(default_factory=dict)
    mixed_pages: tuple[str, ...] = ()
    undeclared_apps: tuple[str, ...] = ()
    undeclared_config: tuple[str, ...] = ()
    undeclared_dict: tuple[str, ...] = ()
    permission_total: int = 0

    @property
    def totals(self) -> dict[str, int]:
        return {family: sum(row.get(family, 0) for row in self.by_app.values()) for family in FAMILIES}

    def ordered_apps(self) -> list[str]:
        """平台层排最后，其余按种子总量降序（报告可读性）。"""

        apps = [app for app in self.by_app if app != PLATFORM]
        apps.sort(key=lambda app: (-sum(self.by_app[app].values()), app))
        return [*apps, PLATFORM] if PLATFORM in self.by_app else apps


def analyse(
    rows_by_model: dict[str, list[Any]],
    manifests: dict[str, SeedManifest],
) -> tuple[OwnershipSummary, list[Problem]]:
    """计算全量种子的归属分布，返回 ``(summary, problems)``。"""

    problems: list[Problem] = []
    prefix_index = _prefix_index(manifests)
    menu_rows = rows_by_model.get(MENU_MODEL) or []
    menu_owner, mixed_pages, menu_problems = _menu_owners(menu_rows, prefix_index)
    problems.extend(menu_problems)

    field_owner, undeclared_apps = _field_owners(rows_by_model.get(FIELD_MODEL) or [], manifests)
    grant_owner, grant_problems = _grant_owners(rows_by_model.get(GRANT_MODEL) or [], menu_owner)
    problems.extend(grant_problems)

    config_owner, undeclared_config = _declared_owners(
        rows_by_model.get(CONFIG_MODEL) or [],
        "key",
        _declared_map(manifests, "config_keys"),
    )
    dict_owner, undeclared_dict = _dict_owner_map(
        rows_by_model.get(DICT_MODEL) or [],
        _declared_map(manifests, "dict_codes"),
    )

    by_app: dict[str, dict[str, int]] = {}

    def bump(owner: str, family: str) -> None:
        by_app.setdefault(owner, {}).setdefault(family, 0)
        by_app[owner][family] += 1

    for owner in menu_owner.values():
        bump(owner, "menu")
    for owner in field_owner.values():
        bump(owner, "field")
    for owner in grant_owner.values():
        bump(owner, "grant")
    for owner in config_owner.values():
        bump(owner, "config")
    for owner in dict_owner.values():
        bump(owner, "dict")

    permission_total = sum(1 for row in menu_rows if _menu_type(row) == MENU_TYPE_PERMISSION)
    summary = OwnershipSummary(
        by_app=by_app,
        mixed_pages=mixed_pages,
        undeclared_apps=undeclared_apps,
        undeclared_config=tuple(sorted(undeclared_config)),
        undeclared_dict=tuple(sorted(undeclared_dict)),
        permission_total=permission_total,
    )
    problems.extend(_aggregate_problems(summary))
    return summary, problems


def menu_owners(
    menu_rows: Sequence[Any],
    manifests: dict[str, SeedManifest],
) -> tuple[dict[str, str], tuple[str, ...], list[Problem]]:
    """菜单行的归属（``pk → app/平台层``）：供归属校验与授权声明拆分复用。"""

    return _menu_owners(menu_rows, _prefix_index(manifests))


def row_owners(
    rows_by_model: dict[str, list[Any]],
    manifests: dict[str, SeedManifest],
) -> dict[str, dict[str, str]]:
    """逐行归属表 ``{model_label: {pk: app/平台层}}``（迁移工具的唯一依据）。

    覆盖可拆分的六类种子：菜单、菜单元数据、字段树、字段白名单、配置键、字典。
    菜单元数据跟随引用它的菜单（被多个应用引用时留平台层）；其余模型不在本表内，
    由迁移工具显式排除（业务示例数据按各自批次单独处理）。
    """

    menu_rows = rows_by_model.get(MENU_MODEL) or []
    menu_owner, _mixed, _problems = menu_owners(menu_rows, manifests)
    field_owner, _undeclared = _field_owners(rows_by_model.get(FIELD_MODEL) or [], manifests)

    meta_owner: dict[str, str] = {}
    meta_votes: dict[str, set[str]] = {}
    for row in menu_rows:
        meta_pk = row.get("fields", {}).get("meta")
        owner = menu_owner.get(row["pk"])
        if meta_pk and owner:
            meta_votes.setdefault(meta_pk, set()).add(owner)
    for meta_pk, owners in meta_votes.items():
        meta_owner[meta_pk] = next(iter(owners)) if len(owners) == 1 else PLATFORM

    grant_owner: dict[str, str] = {}
    for row in rows_by_model.get(GRANT_MODEL) or []:
        menu_pk = row.get("fields", {}).get("menu")
        grant_owner[row["pk"]] = menu_owner.get(menu_pk, PLATFORM) if menu_pk else PLATFORM

    config_declared = _declared_map(manifests, "config_keys")
    dict_declared = _declared_map(manifests, "dict_codes")

    return {
        MENU_MODEL: menu_owner,
        "system.menumeta": meta_owner,
        FIELD_MODEL: field_owner,
        GRANT_MODEL: grant_owner,
        CONFIG_MODEL: _declared_owners(rows_by_model.get(CONFIG_MODEL) or [], "key", config_declared)[0],
        DICT_MODEL: _dict_owner_map(rows_by_model.get(DICT_MODEL) or [], dict_declared)[0],
    }


def _dict_owner_map(rows: Sequence[Any], declared: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    """字典归属：按**根分组**判定，返回 ``(pk → 归属, 未声明归属的编码集合)``。

    字典是「根分组 + 子项」两级结构，逐行判定会把同一个组拆到两个层（子项 code 与
    根 code 不同）。因此先向上找到根分组，再查清单声明；行自身 code 命中时以自身为准
    （扁平无分组字典）。
    """

    by_pk = {row["pk"]: row for row in rows}

    def root_code(row: Any) -> str:
        current = row
        seen: set[str] = set()
        while current.get("fields", {}).get("parent"):
            parent_pk = current["fields"]["parent"]
            if parent_pk in seen:
                break
            seen.add(parent_pk)
            parent = by_pk.get(parent_pk)
            if parent is None:
                break
            current = parent
        return str(current.get("fields", {}).get("code") or "")

    owners: dict[str, str] = {}
    undeclared: set[str] = set()
    for row in rows:
        code = str(row.get("fields", {}).get("code") or "")
        owner = declared.get(code) or declared.get(root_code(row))
        if owner:
            owners[row["pk"]] = owner
        else:
            owners[row["pk"]] = PLATFORM
            undeclared.add(code)
    return owners, undeclared


def _normalize_prefix(prefix: str) -> str:
    value = prefix.strip().strip("/")
    return f"{value}/" if value else ""


def _normalize_path(path: str) -> str:
    return path.strip().lstrip("/").rstrip("$").strip()


def _prefix_index(manifests: dict[str, SeedManifest]) -> tuple[tuple[str, str], ...]:
    entries = []
    for app, manifest in manifests.items():
        for prefix in manifest.url_prefixes:
            normalized = _normalize_prefix(prefix)
            if normalized:
                entries.append((normalized, app))
    # 最长前缀优先：api/system/ 与 api/system/ai/ 并存时命中更具体的一条
    return tuple(sorted(entries, key=lambda item: len(item[0]), reverse=True))


def _owner_by_prefix(path: str, prefix_index: Sequence[tuple[str, str]]) -> str | None:
    normalized = _normalize_path(path)
    for prefix, app in prefix_index:
        if normalized.startswith(prefix):
            return app
    return None


def _menu_type(row: Any) -> int:
    return int(row.get("fields", {}).get("menu_type") or MENU_TYPE_DIRECTORY)


def _menu_owners(
    menu_rows: Sequence[Any],
    prefix_index: Sequence[tuple[str, str]],
) -> tuple[dict[str, str], tuple[str, ...], list[Problem]]:
    """菜单三类的归属：权限点按前缀、页面按众数、目录按平台层。"""

    owners: dict[str, str] = {}
    problems: list[Problem] = []

    for row in menu_rows:
        fields = row.get("fields", {})
        if _menu_type(row) != MENU_TYPE_PERMISSION:
            continue
        owner = _owner_by_prefix(fields.get("path") or "", prefix_index)
        if owner is None:
            problems.append(
                Problem(
                    LEVEL_ERROR,
                    "OWNERSHIP_PREFIX_UNCOVERED",
                    f"权限点 {fields.get('name')}（{fields.get('path')}）未匹配任何应用的 url_prefixes",
                )
            )
            continue
        owners[row["pk"]] = owner

    children: dict[str, list[str]] = {}
    for row in menu_rows:
        parent = row.get("fields", {}).get("parent")
        if parent:
            children.setdefault(parent, []).append(row["pk"])

    mixed: list[str] = []
    for row in menu_rows:
        fields = row.get("fields", {})
        menu_type = _menu_type(row)
        if menu_type == MENU_TYPE_DIRECTORY:
            owners[row["pk"]] = PLATFORM
            continue
        if menu_type != MENU_TYPE_PAGE:
            continue
        votes: dict[str, int] = {}
        for child_pk in children.get(row["pk"], []):
            child_owner = owners.get(child_pk)
            if child_owner:
                votes[child_owner] = votes.get(child_owner, 0) + 1
        total = sum(votes.values())
        if not total:
            owners[row["pk"]] = PLATFORM
            continue
        app, count = max(votes.items(), key=lambda item: item[1])
        if count / total >= PAGE_MAJORITY_RATIO:
            owners[row["pk"]] = app
        else:
            owners[row["pk"]] = PLATFORM
            mixed.append(str(fields.get("name")))
    mixed_problems = [
        Problem(
            LEVEL_WARNING,
            "OWNERSHIP_MIXED_PAGE",
            f"页面 {name} 的权限点跨多个应用，菜单行保留平台层",
        )
        for name in mixed
    ]
    return owners, tuple(mixed), problems + mixed_problems


def _field_rows_by_pk(field_rows: Sequence[Any]) -> dict[str, Any]:
    return {row["pk"]: row for row in field_rows}


def _root_name(row: Any, by_pk: dict[str, Any]) -> str:
    current = row
    seen: set[str] = set()
    while current.get("fields", {}).get("parent"):
        parent_pk = current["fields"]["parent"]
        if parent_pk in seen:
            break
        seen.add(parent_pk)
        parent = by_pk.get(parent_pk)
        if parent is None:
            break
        current = parent
    return str(current.get("fields", {}).get("name") or "")


def _field_owners(
    field_rows: Sequence[Any],
    manifests: dict[str, SeedManifest],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """字段树归属：根名 ``app.model`` → 该 app；``*`` / 未声明应用 → 平台层。"""

    by_pk = _field_rows_by_pk(field_rows)
    owners: dict[str, str] = {}
    undeclared: set[str] = set()
    for row in field_rows:
        root = _root_name(row, by_pk)
        app = root.split(".", 1)[0] if "." in root and not root.startswith("*") else ""
        if app and app in manifests:
            owners[row["pk"]] = app
        else:
            owners[row["pk"]] = PLATFORM
            if app and app not in THIRD_PARTY_MODEL_APPS:
                undeclared.add(app)
    return owners, tuple(sorted(undeclared))


def _grant_owners(
    grant_rows: Sequence[Any],
    menu_owner: dict[str, str],
) -> tuple[dict[str, str], list[Problem]]:
    """字段白名单跟随其权限点/菜单的归属；菜单缺失是硬错误（引用悬空）。"""

    owners: dict[str, str] = {}
    problems: list[Problem] = []
    for row in grant_rows:
        menu_pk = row.get("fields", {}).get("menu")
        owner = menu_owner.get(menu_pk) if menu_pk else None
        if owner is None:
            problems.append(
                Problem(
                    LEVEL_ERROR,
                    "GRANT_MENU_MISSING",
                    f"字段白名单 {row['pk']} 指向的菜单 {menu_pk} 不存在于种子中",
                )
            )
            continue
        owners[row["pk"]] = owner
    return owners, problems


def _declared_map(manifests: dict[str, SeedManifest], attr: str) -> dict[str, str]:
    """汇总各 app 声明的 ``config_keys`` / ``dict_codes``（值 → 应用）。

    同一项被多个应用声明时先到先得；重复声明本身应由清单校验暴露。
    """

    declared: dict[str, str] = {}
    for app, manifest in manifests.items():
        for item in getattr(manifest, attr):
            declared.setdefault(item, app)
    return declared


def _declared_owners(
    rows: Sequence[Any],
    field_name: str,
    declared: dict[str, str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """按清单声明的键/编码归属；未声明的记入待归属清单（默认平台层）。"""

    owners: dict[str, str] = {}
    undeclared: set[str] = set()
    for row in rows:
        value = str(row.get("fields", {}).get(field_name) or "")
        owner = declared.get(value)
        if owner:
            owners[row["pk"]] = owner
        else:
            owners[row["pk"]] = PLATFORM
            undeclared.add(value)
    return owners, tuple(sorted(undeclared))


def _aggregate_problems(summary: OwnershipSummary) -> list[Problem]:
    """聚合型提示：逐条输出会把一次校验刷成几百行。"""

    problems: list[Problem] = []
    if summary.undeclared_apps:
        problems.append(
            Problem(
                LEVEL_WARNING,
                "OWNERSHIP_APP_UNDECLARED",
                f"字段树根归属的应用未提供 seeds/manifest.json，按平台层处理：{'、'.join(summary.undeclared_apps)}",
            )
        )
    if summary.undeclared_config:
        problems.append(
            Problem(
                LEVEL_WARNING,
                "CONFIG_UNOWNED",
                f"{len(summary.undeclared_config)} 个配置键未在清单声明，按平台层处理："
                f"{'、'.join(summary.undeclared_config[:8])}" + ("…" if len(summary.undeclared_config) > 8 else ""),
            )
        )
    if summary.undeclared_dict:
        problems.append(
            Problem(
                LEVEL_WARNING,
                "DICT_UNOWNED",
                f"{len(summary.undeclared_dict)} 个字典编码未在清单声明，按平台层处理",
            )
        )
    return problems


def iter_summary(summary: OwnershipSummary) -> Iterable[tuple[str, dict[str, int]]]:
    """按报告顺序产出 ``(app, 各类别计数)``。"""

    for app in summary.ordered_apps():
        yield app, summary.by_app[app]
