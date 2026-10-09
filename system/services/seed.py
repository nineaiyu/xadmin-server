#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : seed
# author : ly_13
# date : 2026/09/17
"""内置种子装配：模块裁剪 + 冲突预检。

`loaddata` 把**整次导入**放在单个事务里（django/core/management/commands/loaddata.py
的 ``with transaction.atomic(...)``）：任何一行冲突都会回滚全部 fixture，包括菜单与
权限点。而运营完全可能用同一自然键（``code``/``name`` 等）建过对象——典型现场：开箱
模板命令 ``seed_demo_org`` 重建了与内置种子同 code 的示例流程。

本模块提供两道装配前处理，二者都只"剔除待导入的行"，不动库内数据：

1. **模块裁剪**（``ModuleSeedFilter``）：停用模块的菜单/权限点不入库；
2. **冲突预检**（``filter_conflicting_rows``）：自然键已被库内其他主键占用 → 跳过该行，
   并级联跳过引用它的行（FK）或从 m2m 列表中移除该主键。

语义：**库内数据优先，种子让位**；跳过项全部落日志与命令输出，便于人工核对。
无裁剪、无冲突时原样使用仓库里的种子文件（零改动、无临时文件）。
"""

import json
import os
from typing import Any

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS
from django.db.models import Q
from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

# 级联扫描的最大轮次：A 行被跳过 → 引用 A 的 B 行被跳过 → 引用 B 的 C 行……
_MAX_CASCADE_ROUNDS = 5

# 唯一键占用查询每批的键数：上千个键一次 OR 拼接会超 SQLite 表达式树深度
# （单元测试环境即崩），分批查询顺带规避超长 SQL。
_QUERY_CHUNK = 150


def read_seed_rows(model_names: Any, file_root: Any) -> dict[str, Any]:
    """读取种子目录下各模型的文件，返回 ``{model_label: [row, ...]}``。"""

    rows_by_model: dict[str, list[Any]] = {}
    for model in model_names:
        path = os.path.join(file_root, f"{model._meta.model_name}.json")
        if not os.path.exists(path):
            rows_by_model[model._meta.label_lower] = []
            continue
        with open(path, encoding="utf-8") as fp:
            rows_by_model[model._meta.label_lower] = json.load(fp)
    return rows_by_model


def write_seed_rows(rows_by_model: Any, model_names: Any, target_dir: Any) -> list[Any]:
    """把（已过滤的）种子行写到 ``target_dir``，返回可交给 loaddata 的路径清单。

    空模型不写文件也不进清单：没有可导入内容，loaddata 还会对空 fixture 报
    "No fixture data found for '<model>'"（RuntimeWarning 噪音），跳过语义等价。
    """

    labels = []
    os.makedirs(target_dir, exist_ok=True)
    for model in model_names:
        label = model._meta.label_lower
        rows = rows_by_model.get(label) or []
        if not rows:
            continue
        target = os.path.join(target_dir, f"{model._meta.model_name}.json")
        with open(target, "w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=1)
            fp.write("\n")
        labels.append(target)
    return labels


def filter_conflicting_rows(rows_by_model: Any, *, using: Any = DEFAULT_DB_ALIAS) -> tuple[Any, ...]:
    """剔除与库内数据冲突的种子行，返回 ``(rows_by_model, notes)``。

    冲突判定：某行在**唯一键**（字段级 ``unique=True``、``unique_together``，或单字段 /
    多字段 ``UniqueConstraint``）上的取值，已被库里另一主键的对象占用。随后级联处理引用
    被剔除行的行（外键整行剔除、m2m 列表移除对应主键）。

    NULL 参与判定（NULL 与 NULL 视为同一取值）：库约束虽然视 NULL 互不相等，但"同名
    根节点"这类 NULL 键组合在业务上是同一行——放开会让 ``loaddata`` 插入重复行
    （现场：字段标签树 67 个 ``parent=NULL`` 的模型根节点与库内同名的种子行共存成双根）。
    """

    dropped: dict[str, Any] = {}
    notes: list[Any] = []
    pending = dict(rows_by_model)  # 浅拷贝：行本身可能被就地裁剪（m2m 列表）

    # 1) 唯一字段冲突：库内占用者优先
    for label in list(pending):
        model = apps.get_model(label)
        if model is None:
            continue
        for field_names, condition in _unique_checks(model):
            # 每个唯一键过滤后重新取当前行（前一个键可能已剔除若干行）
            rows = pending.get(label) or []
            keys: dict[tuple[Any, ...], tuple[Any, ...]] = {}
            for row in rows:
                values = tuple(row["fields"].get(name) for name in field_names)
                keys.setdefault(tuple(_normalize_unique(value) for value in values), values)
            if not keys:
                continue
            base = model._default_manager.using(using).all()
            if condition is not None:
                base = base.filter(condition)
            existing: dict[tuple[Any, ...], Any] = {}
            if len(field_names) == 1:
                column = field_names[0]
                all_values = [values[0] for values in keys.values()]
                non_null = [value for value in all_values if value is not None]
                if len(non_null) < len(all_values):
                    # Django 把 ``column=None`` 翻译为 IS NULL：NULL 键组合同样参与判定
                    for pk in base.filter(**{f"{column}__isnull": True}).values_list("pk", flat=True):
                        existing[(_normalize_unique(None),)] = pk
                for start in range(0, len(non_null), _QUERY_CHUNK):
                    chunk = non_null[start : start + _QUERY_CHUNK]
                    for *values, pk in base.filter(**{f"{column}__in": chunk}).values_list(column, "pk"):
                        existing[(_normalize_unique(values[0]),)] = pk
            else:
                key_values = list(keys.values())
                for start in range(0, len(key_values), _QUERY_CHUNK):
                    query = Q()
                    for values in key_values[start : start + _QUERY_CHUNK]:
                        query |= Q(**dict(zip(field_names, values, strict=True)))
                    for *values, pk in base.filter(query).values_list(*field_names, "pk"):
                        existing[tuple(_normalize_unique(value) for value in values)] = pk
            if not existing:
                continue
            kept = []
            for row in rows:
                values = tuple(row["fields"].get(name) for name in field_names)
                owner = existing.get(tuple(_normalize_unique(value) for value in values))
                if owner is not None and str(owner) != str(row["pk"]):
                    desc = "、".join(f"{name}={value}" for name, value in zip(field_names, values, strict=True))
                    _mark_dropped(dropped, label, row["pk"])
                    notes.append(f"跳过 {label}({row['pk']})：{desc} 已被库内对象占用（pk={owner}）")
                    continue
                kept.append(row)
            pending[label] = kept

    # 2) 级联：引用被剔除行的行一并剔除（外键整行、m2m 移除主键）
    for _ in range(_MAX_CASCADE_ROUNDS):
        before = sum(len(pks) for pks in dropped.values())
        for label, rows in pending.items():
            if not rows:
                continue
            model = apps.get_model(label)
            if model is None:
                continue
            kept = []
            for row in rows:
                if _row_references_dropped(model, row, dropped, notes):
                    _mark_dropped(dropped, label, row["pk"])
                    notes.append(f"跳过 {label}({row['pk']})：引用了已被跳过的数据")
                    continue
                kept.append(row)
            if len(kept) != len(rows):
                pending[label] = kept
        if sum(len(pks) for pks in dropped.values()) == before:
            break

    for label, rows in pending.items():
        rows_by_model[label] = rows
    return rows_by_model, notes


def _unique_checks(model: Any) -> list[Any]:
    """模型的唯一键清单：``[(字段名元组, 额外过滤 Q), ...]``。

    三类都算唯一键：
    1. 字段级 ``unique=True``（单字段）；
    2. ``UniqueConstraint``——单字段或**多字段组合**（含条件约束，如角色/菜单的
       "未删除数据唯一"、流程节点的 ``(flow, order)``、字典项的 ``(parent, code)``）；
    3. 老式 ``unique_together``——如字段标签的 ``(name, parent)``。迁移层会把它建成
       数据库唯一约束，但不在 ``_meta.constraints`` 里，漏判会让 ``loaddata`` 撞唯一
       约束，单事务回滚**全部**种子。
    """

    checks: list[Any] = []
    for field in model._meta.concrete_fields:
        if getattr(field, "unique", False) and not field.primary_key:
            checks.append(((field.name,), None))
    for constraint in model._meta.constraints:
        fields = getattr(constraint, "fields", None)
        if not fields:
            continue
        checks.append((tuple(fields), getattr(constraint, "condition", None)))
    for fields in model._meta.unique_together:
        checks.append((tuple(fields), None))
    # 去重：同一字段可能同时命中字段级 unique 与约束
    seen, unique_checks = set(), []
    for field_names, condition in checks:
        if field_names in seen:
            continue
        seen.add(field_names)
        unique_checks.append((field_names, condition))
    return unique_checks


def _normalize_unique(value: Any) -> str:
    """唯一键取值归一：种子 JSON 里是字符串，ORM 取回的是 UUID/整数，统一按字符串比较。

    （不归一的话外键类唯一键永远比不中——``str`` 与 ``UUID`` 不相等，冲突预检形同虚设。）
    """

    return str(value)


def _mark_dropped(dropped: dict[str, Any], label: str, pk: str) -> None:
    dropped.setdefault(label, set()).add(str(pk))


def _row_references_dropped(model: Any, row: Any, dropped: dict[str, Any], notes: list[Any]) -> bool:
    """该行是否引用了被剔除的行；m2m 列表则就地移除被剔除主键。"""

    if not dropped:
        return False
    fields = row["fields"]
    for field in model._meta.concrete_fields:
        if not getattr(field, "is_relation", False) or not getattr(field, "many_to_one", False):
            continue
        value = fields.get(field.name)
        if value is None:
            continue
        target = field.related_model._meta.label_lower
        if str(value) in dropped.get(target, set()):
            return True
    for field in model._meta.many_to_many:
        value = fields.get(field.name)
        if not isinstance(value, list) or not value:
            continue
        target = field.related_model._meta.label_lower
        removed = dropped.get(target)
        if not removed:
            continue
        remaining = [item for item in value if not (isinstance(item, str) and item in removed)]
        if len(remaining) != len(value):
            notes.append(
                f"{model._meta.label_lower}({row['pk']})：{field.name} 移除 {len(value) - len(remaining)} 个已跳过主键"
            )
            fields[field.name] = remaining
    return False


def backfill_null_timestamps(model_names: Any, *, using: Any = DEFAULT_DB_ALIAS) -> int:
    """回填种子行缺失的创建/更新时间，返回回填处数。

    ``loaddata`` 以 raw 方式保存对象（``save_base(raw=True)`` 跳过 ``pre_save``），
    ``auto_now_add``/``auto_now`` 不生效；种子 JSON 未显式给出时间的行落库为 NULL
    ——列表页「更新时间」列整列空白（实测 dataset/screen/fieldpermission 等）。
    导入后统一补当前时间；只更新 NULL 行，不动已有时间（含库内既有对象）。
    """
    now = timezone.now()
    updated = 0
    for model in model_names:
        field_names = {field.name for field in model._meta.concrete_fields}
        queryset = model._default_manager.using(using)
        if "created_time" in field_names:
            updated += queryset.filter(created_time__isnull=True).update(created_time=now)
        if "updated_time" in field_names:
            updated += queryset.filter(updated_time__isnull=True).update(updated_time=now)
    return updated


def build_seed_fixtures(
    model_names: Any, file_root: Any, target_dir: Any, *, module_filter: Any = None, using: Any = DEFAULT_DB_ALIAS
) -> tuple[Any, ...]:
    """装配待导入的种子：读文件 → 模块裁剪（可选）→ 冲突预检 → 落盘。

    :return: ``(fixture_labels, notes, trimmed)``；``trimmed=False`` 时 ``fixture_labels``
        是仓库里的原始文件路径（无裁剪、无冲突，零改动）。空种子模型（无行）不进
        清单：没有可导入内容，loaddata 还会对空 fixture 报 "No fixture data found"
        （RuntimeWarning 噪音），跳过语义等价
    """

    rows_by_model = read_seed_rows(model_names, file_root)
    notes: list[Any] = []
    trimmed = False

    if module_filter is not None:
        # 先算一次菜单（记录 meta 引用关系），再按声明顺序过滤（menumeta 依赖菜单侧结果）
        rows_by_model = _apply_module_filter(module_filter, rows_by_model, model_names)
        trimmed = True

    rows_by_model, conflict_notes = filter_conflicting_rows(rows_by_model, using=using)
    if conflict_notes:
        notes.extend(conflict_notes)
        trimmed = True

    non_empty = [model for model in model_names if rows_by_model.get(model._meta.label_lower)]
    if not trimmed:
        labels = [os.path.join(file_root, f"{model._meta.model_name}.json") for model in non_empty]
        return labels, notes, False

    for note in notes:
        logger.warning("seed conflict: %s", note)
    return write_seed_rows(rows_by_model, non_empty, target_dir), notes, True


def _apply_module_filter(module_filter: Any, rows_by_model: Any, model_names: Any) -> dict[str, Any]:
    menu_label = module_filter.MENU_MODEL
    if menu_label in rows_by_model:
        # 菜单必须先行（记录 meta 引用关系），且过滤结果要写回
        rows_by_model[menu_label] = module_filter.filter_rows(menu_label, rows_by_model[menu_label])
    for model in model_names:
        label = model._meta.label_lower
        if label == menu_label:
            continue
        rows_by_model[label] = module_filter.filter_rows(label, rows_by_model.get(label, []))
    typed_value: dict[str, Any] = rows_by_model
    return typed_value
