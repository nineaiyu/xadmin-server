#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子多对多关系：授权型剥离、批量载荷抽取与装载后一次落库。

``loaddata`` 处理 m2m 是逐行的：每行一次「读既有关系（JOIN）+ DELETE + 多值 INSERT」。
完整种子有近 500 行带 m2m（字段白名单 4431 条关系、菜单模型绑定 417 条），装载要发
上千条小往返；多个 worker 并发跑种子测试时，这些 JOIN 会成为数据库侧的尖峰
（全量 pytest 尾部假死的直接燃料）。

因此把 m2m 列表从 fixture 中抽出、装载完成后按「父行一次 DELETE + 一次多值 INSERT」
批量落库，语义与 ``set()`` 等价（覆盖式：父行在种子里的关系 = 种子声明的集合）。

两类关系分开处理，语义相反，不能混用：

- **授权型**（``_M2M_STRIP_FIELDS``）：角色被授予哪些菜单是客户资产，种子只声明基线，
  必须整体剥离、由 ``grants.json`` 的并集通道落地，绝不能覆盖；
- **属性型**（其余自动中间表）：菜单绑定哪些模型、字段权限白名单，种子值即期望值，
  抽取后批量覆盖写入。
"""

from typing import Any

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS

from common.utils import get_logger

from .types import NOTE_STRIP, SeedM2MBatch, SeedNote

logger = get_logger(__name__)

# 装载前必须剥离的多对多字段：{模型 label: [字段名, ...]}。
# 这些关系是**客户资产**（角色被授予哪些菜单），种子只能声明基线、不能覆盖存量；
# 期望值由 ``grants.json`` + ``apply_grants`` 的并集语义落地。新增授权型 m2m 时
# 登记到这里——``loaddata`` 见到键就 set，空数组也会清零，漏登记即数据丢失。
_M2M_STRIP_FIELDS: dict[str, tuple[str, ...]] = {
    "identity.userrole": ("menu",),
}

# 单条 INSERT 携带的关系数：PG 参数上限远高于此，取整千便于观测与重试。
_BULK_BATCH = 1000


def strip_m2m_fields(rows_by_model: Any) -> tuple[Any, ...]:
    """从种子行中剥离角色菜单授权，返回 ``(rows_by_model, notes)``。

    ``loaddata`` 对多对多是**覆盖式赋值**：只要 fixture 里出现该键（哪怕是空数组
    ``[]``），反序列化就执行 ``set(object_list)``，把库内既有关系整体替换成种子值。
    现场后果：存量库升级后「默认权限」角色丢失了全部人工授予的菜单——种子
    ``userrole.json`` 给 4 个种子角色带的是 ``"menu": []``，重灌即清零，随后
    ``apply_grants`` 只加回 grants 声明的那 17 项，种子里没有的二开菜单永久丢失。

    只剥离角色菜单授权，不做全模型 m2m 剥离：``menu.model``（权限菜单→模型）、
    ``fieldpermission.field``（角色×权限点→字段）等属性型 m2m 是功能契约，种子值即
    期望值，覆盖语义正是所需；而授权是**客户资产**，必须走 ``grants.json`` +
    ``apply_grants`` 的并集通道。两类语义相反，故按模型显式声明，不用「凡是 m2m 就剥」。
    """

    notes: list[Any] = []
    for label in _M2M_STRIP_FIELDS:
        rows = rows_by_model.get(label)
        if not rows:
            continue
        for row in rows:
            fields = row.get("fields")
            if not isinstance(fields, dict):
                continue
            for name in _M2M_STRIP_FIELDS[label]:
                if name in fields:
                    del fields[name]
                    notes.append(
                        SeedNote(
                            f"{label}({row.get('pk')})：剥离多对多字段 {name}（改由声明式授权应用，避免覆盖库内数据）",
                            NOTE_STRIP,
                        )
                    )
    return rows_by_model, notes


def extract_m2m_relations(rows_by_model: Any) -> tuple[Any, list[SeedM2MBatch]]:
    """把种子行里的 m2m 列表抽成批量载荷，返回 ``(rows_by_model, batches)``。

    只处理**自动生成中间表**的 m2m；显式 ``through`` 模型（如部门主管任命）可能带
    额外列，语义不是 `set()`，保留给 ``loaddata`` 逐行处理。

    行里出现该键就记为一批（空列表同样记父行）：覆盖式语义下空列表表示「清空该行
    关系」，抽取时丢掉空列表会把存量关系留在库里。
    """

    batches: list[SeedM2MBatch] = []
    for label, rows in rows_by_model.items():
        if not rows:
            continue
        model = apps.get_model(label)
        if model is None:
            continue
        fields = [field for field in model._meta.many_to_many if field.remote_field.through._meta.auto_created]
        if not fields:
            continue
        parents: dict[str, list[str]] = {field.name: [] for field in fields}
        pairs: dict[str, list[tuple[str, str]]] = {field.name: [] for field in fields}
        for row in rows:
            values = row.get("fields")
            if not isinstance(values, dict):
                continue
            for field in fields:
                if field.name not in values:
                    continue
                parents[field.name].append(str(row["pk"]))
                for item in values.pop(field.name) or []:
                    pairs[field.name].append((str(row["pk"]), str(item)))
        for field in fields:
            if parents[field.name]:
                batches.append(
                    SeedM2MBatch(
                        model=label,
                        field_name=field.name,
                        parents=parents[field.name],
                        pairs=pairs[field.name],
                    )
                )
    return rows_by_model, batches


def apply_m2m_relations(batches: Any, *, using: Any = DEFAULT_DB_ALIAS) -> int:
    """按批量载荷写入多对多关系，返回计划写入的关系条数。

    与 ``loaddata`` 的 ``set()`` 等价：先清掉父行在种子里的全部既有关系，再一次性插入
    声明的关系（``ignore_conflicts`` 吸收 fixture 内的重复项）。

    只对**已存在**的父行写入：装配层替换 ``loaddata``（单元测试的标签捕获）或部分
    ``--apps`` 装载时父行可能不在库中，此时跳过而不是撞外键。子行缺失仍按外键错误
    抛出——那说明种子自身引用有误，需要暴露。
    """

    written = 0
    for batch in batches:
        model = apps.get_model(batch.model)
        if model is None:
            continue
        field = model._meta.get_field(batch.field_name)
        through = field.remote_field.through
        parent_column = field.m2m_column_name()
        child_column = field.m2m_reverse_name()
        parent_pk = model._meta.pk
        child_pk = field.related_model._meta.pk
        manager = through._default_manager.using(using)

        wanted = [parent_pk.to_python(value) for value in dict.fromkeys(batch.parents)]
        present = {
            str(value)
            for value in model._default_manager.using(using)
            .filter(pk__in=wanted)
            .values_list(parent_pk.name, flat=True)
        }
        parents = [value for value in wanted if str(value) in present]
        if not parents:
            logger.debug("seed m2m skipped, parents absent: %s.%s", batch.model, batch.field_name)
            continue
        keep = {str(value) for value in parents}
        rows = [
            through(**{parent_column: parent_pk.to_python(parent), child_column: child_pk.to_python(child)})
            for parent, child in dict.fromkeys(batch.pairs)
            if str(parent_pk.to_python(parent)) in keep
        ]
        manager.filter(**{f"{parent_column}__in": parents}).delete()
        if rows:
            manager.bulk_create(rows, ignore_conflicts=True, batch_size=_BULK_BATCH)
            written += len(rows)
    return written
