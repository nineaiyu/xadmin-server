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

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS

from common.utils import get_logger

logger = get_logger(__name__)

# 级联扫描的最大轮次：A 行被跳过 → 引用 A 的 B 行被跳过 → 引用 B 的 C 行……
_MAX_CASCADE_ROUNDS = 5


def read_seed_rows(model_names, file_root) -> dict:
    """读取种子目录下各模型的文件，返回 ``{model_label: [row, ...]}``。"""

    rows_by_model = {}
    for model in model_names:
        path = os.path.join(file_root, f"{model._meta.model_name}.json")
        if not os.path.exists(path):
            rows_by_model[model._meta.label_lower] = []
            continue
        with open(path, encoding="utf-8") as fp:
            rows_by_model[model._meta.label_lower] = json.load(fp)
    return rows_by_model


def write_seed_rows(rows_by_model, model_names, target_dir) -> list:
    """把（已过滤的）种子行写到 ``target_dir``，返回可交给 loaddata 的路径清单。"""

    labels = []
    os.makedirs(target_dir, exist_ok=True)
    for model in model_names:
        label = model._meta.label_lower
        target = os.path.join(target_dir, f"{model._meta.model_name}.json")
        with open(target, "w", encoding="utf-8") as fp:
            json.dump(rows_by_model.get(label, []), fp, ensure_ascii=False, indent=1)
            fp.write("\n")
        labels.append(target)
    return labels


def filter_conflicting_rows(rows_by_model, *, using=DEFAULT_DB_ALIAS) -> tuple:
    """剔除与库内数据冲突的种子行，返回 ``(rows_by_model, notes)``。

    冲突判定：某行在**唯一字段**（``unique=True`` 且非主键）上的取值，已被库里另一主键的对象占用。
    随后级联处理引用被剔除行的行（外键整行剔除、m2m 列表移除对应主键）。
    """

    dropped: dict = {}
    notes: list = []
    pending = dict(rows_by_model)  # 浅拷贝：行本身可能被就地裁剪（m2m 列表）

    # 1) 唯一字段冲突：库内占用者优先
    for label in list(pending):
        model = apps.get_model(label)
        if model is None:
            continue
        for field_name, condition in _unique_checks(model):
            # 每个唯一键过滤后重新取当前行（前一个键可能已剔除若干行）
            rows = pending.get(label) or []
            values = {row["fields"].get(field_name) for row in rows if row["fields"].get(field_name) is not None}
            if not values:
                continue
            queryset = model._default_manager.using(using).filter(**{f"{field_name}__in": list(values)})
            if condition is not None:
                queryset = queryset.filter(condition)
            existing = dict(queryset.values_list(field_name, "pk"))
            if not existing:
                continue
            kept = []
            for row in rows:
                value = row["fields"].get(field_name)
                if value is not None and value in existing and str(existing[value]) != str(row["pk"]):
                    _mark_dropped(dropped, label, row["pk"])
                    notes.append(
                        f"跳过 {label}({row['pk']})：{field_name}={value} 已被库内对象占用（pk={existing[value]}）"
                    )
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


def _unique_checks(model) -> list:
    """模型的唯一键清单：``[(字段名, 额外过滤 Q), ...]``。

    两类都算唯一键：
    1. 字段级 ``unique=True``；
    2. 单字段 ``UniqueConstraint``（含条件约束，如角色/菜单的"未删除数据唯一"）。
    """

    checks: list = []
    for field in model._meta.concrete_fields:
        if getattr(field, "unique", False) and not field.primary_key:
            checks.append((field.name, None))
    for constraint in model._meta.constraints:
        fields = getattr(constraint, "fields", None)
        if not fields or len(fields) != 1:
            continue
        checks.append((fields[0], getattr(constraint, "condition", None)))
    # 去重：同一字段可能同时命中字段级 unique 与约束
    seen, unique_checks = set(), []
    for field_name, condition in checks:
        if field_name in seen:
            continue
        seen.add(field_name)
        unique_checks.append((field_name, condition))
    return unique_checks


def _mark_dropped(dropped: dict, label: str, pk: str) -> None:
    dropped.setdefault(label, set()).add(str(pk))


def _row_references_dropped(model, row, dropped: dict, notes: list) -> bool:
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


def build_seed_fixtures(model_names, file_root, target_dir, *, module_filter=None, using=DEFAULT_DB_ALIAS) -> tuple:
    """装配待导入的种子：读文件 → 模块裁剪（可选）→ 冲突预检 → 落盘。

    :return: ``(fixture_labels, notes, trimmed)``；``trimmed=False`` 时 ``fixture_labels``
        是仓库里的原始文件路径（无裁剪、无冲突，零改动）
    """

    rows_by_model = read_seed_rows(model_names, file_root)
    notes: list = []
    trimmed = False

    if module_filter is not None:
        # 先算一次菜单（记录 meta 引用关系），再按声明顺序过滤（menumeta 依赖菜单侧结果）
        rows_by_model = _apply_module_filter(module_filter, rows_by_model, model_names)
        trimmed = True

    rows_by_model, conflict_notes = filter_conflicting_rows(rows_by_model, using=using)
    if conflict_notes:
        notes.extend(conflict_notes)
        trimmed = True

    if not trimmed:
        labels = [os.path.join(file_root, f"{model._meta.model_name}.json") for model in model_names]
        return labels, notes, False

    for note in notes:
        logger.warning("seed conflict: %s", note)
    return write_seed_rows(rows_by_model, model_names, target_dir), notes, True


def _apply_module_filter(module_filter, rows_by_model, model_names) -> dict:
    menu_label = module_filter.MENU_MODEL
    if menu_label in rows_by_model:
        # 菜单必须先行（记录 meta 引用关系），且过滤结果要写回
        rows_by_model[menu_label] = module_filter.filter_rows(menu_label, rows_by_model[menu_label])
    for model in model_names:
        label = model._meta.label_lower
        if label == menu_label:
            continue
        rows_by_model[label] = module_filter.filter_rows(label, rows_by_model.get(label, []))
    return rows_by_model
