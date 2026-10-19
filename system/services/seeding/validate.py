#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""装载计划的结构与归属校验（只读，不写库）。

覆盖四层：

1. **清单**：版本、app 归属、kind、路径越界（发现阶段产生）；
2. **文件与行**：文件缺失 / 不可解析、模型未注册、行结构、跨文件主键重复；
3. **归属**：每行能否判给某个 app 或平台层（见 :mod:`ownership`）；
4. **引用**：字段白名单指向的菜单必须存在（跨 app 外键在装载前可解析）。

``--check`` 就是「plan + validate」，因此可在无数据库的 CI 环境运行。
"""

import json
from dataclasses import dataclass
from typing import Any

from django.apps import apps as django_apps

from .discover import Discovery
from .ownership import OwnershipSummary, analyse
from .types import LEVEL_ERROR, LEVEL_WARNING, Problem, SeedFileSpec, SeedManifest, SeedPlan

# 单个文件的行级问题最多输出条数：系统性错位（如整表 model 写错）不应刷屏
_MAX_ROW_PROBLEMS = 20


@dataclass(frozen=True)
class ValidationResult:
    """一次校验的结果：问题清单 + 归属分布（未要求归属时为空）。"""

    problems: tuple[Problem, ...] = ()
    ownership: OwnershipSummary | None = None

    def has_error(self) -> bool:
        return any(problem.level == LEVEL_ERROR for problem in self.problems)


def validate_plan(
    plan: SeedPlan,
    *,
    manifests: dict[str, SeedManifest] | None = None,
) -> ValidationResult:
    """校验计划内全部种子文件。

    :param manifests: 传入时额外做归属计算与归属校验；不传则只做结构校验。
    """

    problems: list[Problem] = []
    rows_by_model: dict[str, list[Any]] = {}
    seen: dict[tuple[str, str], SeedFileSpec] = {}

    for spec in [*plan.all_specs(), *plan.samples]:
        spec_problems, rows = _validate_spec(spec, seen)
        problems.extend(spec_problems)
        if rows:
            rows_by_model.setdefault(spec.model, []).extend(rows)

    problems.extend(_check_references(rows_by_model))
    problems.extend(_check_grants(plan, rows_by_model))

    summary = None
    if manifests is not None:
        summary, ownership_problems = analyse(rows_by_model, manifests)
        problems.extend(ownership_problems)
        problems.extend(_check_declarations(manifests, rows_by_model))
    return ValidationResult(tuple(problems), summary)


def _check_references(rows_by_model: dict[str, list[Any]]) -> list[Problem]:
    """跨文件引用完整性：外键 / m2m 指向的主键必须在本次装载集合内。

    只校验「目标模型也在种子面内」的引用：指向用户、部门等运行期数据的外键
    （``creator`` / ``modifier`` 等）不参与，否则会把每一行都判成悬空。
    内嵌在 JSON 字段里的主键（数据权限规则）不做 ORM 级校验，由归属报告与
    装载期冲突预检兜底。
    """

    index: dict[str, set[str]] = {
        label: {str(row.get("pk")) for row in rows if isinstance(row, dict)} for label, rows in rows_by_model.items()
    }
    problems: list[Problem] = []
    reported = 0
    for label, rows in rows_by_model.items():
        model = _get_model(label)
        if model is None:
            continue
        relations = _relation_fields(model)
        if not relations:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for name, target_label, multiple in relations:
                if target_label not in index:
                    continue
                missing = _missing_targets(row.get("fields", {}).get(name), index[target_label], multiple)
                if not missing:
                    continue
                reported += 1
                if reported <= _MAX_ROW_PROBLEMS:
                    problems.append(
                        Problem(
                            LEVEL_ERROR,
                            "SEED_REF_MISSING",
                            f"{label}({row.get('pk')}) 的 {name} 指向 {target_label} 中不存在的对象："
                            f"{'、'.join(missing[:3])}",
                        )
                    )
    if reported > _MAX_ROW_PROBLEMS:
        problems.append(
            Problem(
                LEVEL_ERROR,
                "SEED_REF_TRUNCATED",
                f"另有 {reported - _MAX_ROW_PROBLEMS} 处引用缺失未逐条列出",
            )
        )
    return problems


def _get_model(label: str) -> Any:
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _relation_fields(model: Any) -> list[tuple[str, str, bool]]:
    """``[(字段名, 目标模型 label, 是否多值), ...]``，只取种子会携带的关联字段。"""

    relations = []
    for field in model._meta.concrete_fields:
        if getattr(field, "many_to_one", False) and getattr(field, "is_relation", False):
            relations.append((field.name, field.related_model._meta.label_lower, False))
    for field in model._meta.many_to_many:
        relations.append((field.name, field.related_model._meta.label_lower, True))
    return relations


def _missing_targets(value: Any, targets: set[str], multiple: bool) -> list[str]:
    if value is None:
        return []
    if multiple:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item) not in targets]
    if isinstance(value, list):
        return []
    return [] if str(value) in targets else [str(value)]


def _check_grants(plan: SeedPlan, rows_by_model: dict[str, list[Any]]) -> list[Problem]:
    """授权声明静态校验：角色与菜单名必须落在本次装载的种子面内。"""

    from .grants import load_grants, validate_grants

    if not plan.grants:
        return []
    declarations, problems = load_grants(plan.grants)
    menu_names = {str(row.get("fields", {}).get("name")) for row in rows_by_model.get("system.menu", [])}
    role_codes = {str(row.get("fields", {}).get("code")) for row in rows_by_model.get("identity.userrole", [])}
    problems.extend(validate_grants(declarations, menu_names=menu_names, role_codes=role_codes))
    return problems


def _check_declarations(manifests: dict[str, SeedManifest], rows_by_model: dict[str, list[Any]]) -> list[Problem]:
    """清单声明的交叉校验：同一项被两个应用同时声明、或声明了不存在的项。"""

    problems: list[Problem] = []
    for attr, field_name, model in (
        ("config_keys", "key", "system.systemconfig"),
        ("dict_codes", "code", "system.datadict"),
    ):
        owners: dict[str, list[str]] = {}
        for app, manifest in manifests.items():
            for item in getattr(manifest, attr):
                owners.setdefault(item, []).append(app)
        for item, apps in sorted(owners.items()):
            if len(apps) > 1:
                problems.append(
                    Problem(
                        LEVEL_ERROR,
                        "OWNERSHIP_DECLARATION_CONFLICT",
                        f"{item} 被多个应用同时声明（{'、'.join(apps)}），归属必须唯一",
                    )
                )
        rows = rows_by_model.get(model)
        if not rows:
            continue
        existing = {str(row.get("fields", {}).get(field_name) or "") for row in rows}
        for item in sorted(set(owners) - existing):
            problems.append(
                Problem(
                    LEVEL_WARNING,
                    "OWNERSHIP_DECLARATION_UNKNOWN",
                    f"清单声明了不存在的{field_name}：{item}",
                )
            )
    return problems


def collect_problems(discovery: Discovery, plan: SeedPlan) -> list[Problem]:
    """发现期问题 + 计划期问题（保持列表形态，供只需要问题的调用方使用）。"""

    result = validate_plan(plan, manifests=discovery.manifests)
    return [*discovery.problems, *result.problems]


def _validate_spec(
    spec: SeedFileSpec,
    seen: dict[tuple[str, str], SeedFileSpec],
) -> tuple[list[Problem], list[Any] | None]:
    if not spec.path.is_file():
        return [Problem(LEVEL_ERROR, "SEED_FILE_MISSING", "种子文件不存在", file=spec.path)], None

    model_problems = _check_model(spec)
    if model_problems:
        return model_problems, None

    try:
        rows = json.loads(spec.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Problem(LEVEL_ERROR, "SEED_UNREADABLE", f"种子文件无法解析：{exc}", file=spec.path)], None

    if not isinstance(rows, list):
        return [Problem(LEVEL_ERROR, "SEED_INVALID", "种子文件顶层必须是数组", file=spec.path)], None

    return _validate_rows(spec, rows, seen), rows


def _check_model(spec: SeedFileSpec) -> list[Problem]:
    try:
        model = django_apps.get_model(spec.model)
    except (LookupError, ValueError):
        model = None
    if model is None:
        return [Problem(LEVEL_ERROR, "SEED_MODEL_UNKNOWN", f"模型未注册：{spec.model}", file=spec.path)]
    return []


def _validate_rows(
    spec: SeedFileSpec,
    rows: list[Any],
    seen: dict[tuple[str, str], SeedFileSpec],
) -> list[Problem]:
    problems: list[Problem] = []
    row_problem_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "pk" not in row or "fields" not in row:
            row_problem_count += 1
            if row_problem_count <= _MAX_ROW_PROBLEMS:
                problems.append(
                    Problem(
                        LEVEL_ERROR,
                        "SEED_ROW_INVALID",
                        "行必须是含 pk / fields 的对象",
                        file=spec.path,
                        row_index=index,
                    )
                )
            continue
        if row.get("model") != spec.model:
            row_problem_count += 1
            if row_problem_count <= _MAX_ROW_PROBLEMS:
                problems.append(
                    Problem(
                        LEVEL_ERROR,
                        "SEED_MODEL_MISMATCH",
                        f"行的 model={row.get('model')!r} 与清单声明的 {spec.model!r} 不一致",
                        file=spec.path,
                        row_index=index,
                    )
                )
            continue
        problems.extend(_check_duplicate_pk(spec, str(row["pk"]), seen))

    if row_problem_count > _MAX_ROW_PROBLEMS:
        problems.append(
            Problem(
                LEVEL_ERROR,
                "SEED_ROW_TRUNCATED",
                f"另有 {row_problem_count - _MAX_ROW_PROBLEMS} 处行级问题未逐条列出",
                file=spec.path,
            )
        )
    return problems


def _check_duplicate_pk(
    spec: SeedFileSpec,
    pk: str,
    seen: dict[tuple[str, str], SeedFileSpec],
) -> list[Problem]:
    key = (spec.model, pk)
    previous = seen.get(key)
    if previous is None:
        seen[key] = spec
        return []
    if previous.app == spec.app:
        return [
            Problem(
                LEVEL_ERROR,
                "SEED_PK_DUPLICATE",
                f"同一层内主键重复：{pk}（{previous.path.name} 与 {spec.path.name}）",
                file=spec.path,
            )
        ]
    return [
        Problem(
            LEVEL_WARNING,
            "SEED_PK_OVERRIDE",
            f"主键 {pk} 在 {previous.app} 与 {spec.app} 各出现一次，按装载顺序后者覆盖前者",
            file=spec.path,
        )
    ]
