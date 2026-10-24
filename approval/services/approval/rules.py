#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批规则（多级审批链）保存编排：规则 + 级次的原子写（事务边界在此收口）。

级次按 order upsert：同 order 原位更新、缺失删除、新增创建，级次行主键与
创建审计跨编辑稳定；序列化层只保留字段校验（路径正则可编译、级次审批人
真实可用），事务编排收敛到本模块。
"""

from typing import Any

from django.db import transaction

from approval.models.approval_rule import ApprovalRule, ApprovalRuleLevel


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def create_rule(validated_data: Any) -> Any:
    """创建规则：主表落行 + 级次初始集（同一事务）。"""
    levels = validated_data.pop("levels", [])
    rule = ApprovalRule.objects.create(**validated_data)
    sync_rule_levels(rule, levels)
    return rule


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def update_rule(instance: Any, validated_data: Any) -> Any:
    """更新规则：主表字段保存 + 级次同步（levels 未携带时保持原样，同一事务）。"""
    levels = validated_data.pop("levels", None)
    for attr, value in validated_data.items():
        setattr(instance, attr, value)
    instance.save()
    if levels is not None:
        sync_rule_levels(instance, levels)
    return instance


def sync_rule_levels(rule: Any, levels: Any) -> None:
    """级次按 order upsert：order 相同的既有行原位更新，缺失的删除，新出现的创建。

    不再 delete+recreate——级次行主键与创建审计在编辑间保持稳定（重复编辑
    不重置 created_time/created_by），order 在规则内唯一（DB 约束）天然可作
    upsert 键；在途单不受影响（级次快照在建单时已落到独立表）。
    """
    existing = {level.order: level for level in rule.levels.all()}
    incoming_orders = set()
    for level in levels:
        level.pop("pk", None)
        order = int(level["order"])
        level["order"] = order
        incoming_orders.add(order)
        row = existing.get(order)
        if row is None:
            ApprovalRuleLevel.objects.create(rule=rule, **level)
            continue
        for attr, value in level.items():
            setattr(row, attr, value)
        row.save()
    for order, row in existing.items():
        if order not in incoming_orders:
            row.delete()
