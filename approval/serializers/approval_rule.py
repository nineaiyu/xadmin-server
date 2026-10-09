#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批规则序列化器（多级审批链配置）。

- ApprovalRuleSerializer：规则 + 级次列表嵌套写入（levels 按 order upsert：
  同 order 原位更新、缺失删除、新增创建，级次行主键与创建审计跨编辑稳定）；
  保存时校验路径正则可编译、级次审批人真实可用（用户名 / 角色 code / 岗位 code，
  与引擎解析同口径过滤 is_active），避免「配错人名/配了停用账号」拖到建单
  （拦截发生）时才发现；
- 规则改动只影响之后新建的审批单：在途单的级次是建单瞬间的快照，不受影响。
"""

import re
from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from approval.models.approval_rule import (
    RULE_METHODS,
    ApprovalRule,
    ApprovalRuleLevel,
)
from common.core.serializers import BaseModelSerializer


class ApprovalRuleLevelSerializer(BaseModelSerializer):
    class Meta:
        model = ApprovalRuleLevel
        fields = ["pk", "name", "order", "approve_type", "assignee_type", "assignee_value"]
        read_only_fields = ["pk"]
        extra_kwargs = {"order": {"required": False}}


class ApprovalRuleSerializer(BaseModelSerializer):
    levels = ApprovalRuleLevelSerializer(many=True, required=False)
    level_count = serializers.SerializerMethodField(label=_("Level count"))

    class Meta:
        model = ApprovalRule
        fields = [
            "pk",
            "name",
            "path_patterns",
            "methods",
            "priority",
            "is_active",
            "remark",
            "levels",
            "level_count",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk"]
        table_fields = [
            "name",
            "path_patterns",
            "methods",
            "level_count",
            "priority",
            "is_active",
            "remark",
            "created_time",
        ]

    def get_level_count(self, obj: Any) -> int:
        annotated = getattr(obj, "levels_count", None)
        count: int = annotated if annotated is not None else obj.levels.count()
        return count

    def validate_path_patterns(self, value: Any) -> Any:
        """路径正则清单：至少一条，且逐条可编译（过滤空白项）。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Path patterns must be a list"))
        patterns = []
        for item in value:
            pattern = str(item or "").strip()
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                raise serializers.ValidationError(_("Invalid path pattern: {}").format(pattern)) from None
            patterns.append(pattern)
        if not patterns:
            raise serializers.ValidationError(_("At least one path pattern is required"))
        return patterns

    def validate_methods(self, value: Any) -> Any:
        """HTTP 方法清单：白名单校验 + 统一大写去重；空清单 = 不限定方法（存量语义）。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Methods must be a list"))
        methods = []
        for item in value:
            method = str(item or "").strip().upper()
            if not method:
                continue
            if method not in RULE_METHODS:
                raise serializers.ValidationError(_("Invalid method: {}").format(method))
            if method not in methods:
                methods.append(method)
        return methods

    def validate_levels(self, value: Any) -> Any:
        """级次校验：至少 1 级；order 缺省按顺序补齐且不可重复；审批人必须真实存在。"""
        if value is None:
            return value
        if not value:
            raise serializers.ValidationError(_("The rule requires at least one approval level"))
        orders = []
        for index, level in enumerate(value):
            order = level.get("order")
            level["order"] = order if order not in (None, "") else index + 1
            if int(level["order"]) < 1:
                raise serializers.ValidationError(_("Level order must be greater than 0"))
            orders.append(int(level["order"]))
            self._validate_assignee(level)
        if len(set(orders)) != len(orders):
            raise serializers.ValidationError(_("Level order is duplicated"))
        return value

    def _validate_assignee(self, level: Any) -> None:
        """审批人存在性校验：与引擎 resolve_level_users 的解析口径完全一致（含
        is_active 过滤）——停用账号/角色若在这里放行，建单时该级候选人被过滤为空，
        fail-closed 报「该级无可用审批人」，配置错误被推迟到拦截发生时才暴露。"""
        values = [value.strip() for value in str(level.get("assignee_value") or "").split(",") if value.strip()]
        if not values:
            raise serializers.ValidationError(
                _("Level {order} requires at least one approver").format(order=level.get("order"))
            )
        assignee_type = level.get("assignee_type")
        if assignee_type == ApprovalRuleLevel.AssigneeType.ROLE:
            from identity.models import UserRole

            existing = set(
                UserRole.objects.filter(code__in=values, is_active=True, deleted_at__isnull=True).values_list(
                    "code", flat=True
                )
            )
            missing = [value for value in values if value not in existing]
            if missing:
                raise serializers.ValidationError(_("Role does not exist: {}").format(", ".join(missing)))
            return
        if assignee_type == ApprovalRuleLevel.AssigneeType.POST:
            from identity.models import Post

            existing = set(
                Post.objects.filter(code__in=values, is_active=True, deleted_at__isnull=True).values_list(
                    "code", flat=True
                )
            )
            missing = [value for value in values if value not in existing]
            if missing:
                raise serializers.ValidationError(_("Post does not exist: {}").format(", ".join(missing)))
            return
        if assignee_type == ApprovalRuleLevel.AssigneeType.USER:
            from identity.models import UserInfo

            existing = set(
                UserInfo.objects.filter(username__in=values, is_active=True).values_list("username", flat=True)
            )
            missing = [value for value in values if value not in existing]
            if missing:
                raise serializers.ValidationError(_("User does not exist: {}").format(", ".join(missing)))
            return
        raise serializers.ValidationError(_("Unknown assignee type: {}").format(str(assignee_type) or "(empty)"))

    @transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def create(self, validated_data: Any) -> Any:
        levels = validated_data.pop("levels", [])
        rule = super().create(validated_data)
        self._sync_levels(rule, levels)
        return rule

    @transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def update(self, instance: Any, validated_data: Any) -> Any:
        levels = validated_data.pop("levels", None)
        rule = super().update(instance, validated_data)
        if levels is not None:
            self._sync_levels(rule, levels)
        return rule

    def _sync_levels(self, rule: Any, levels: Any) -> None:
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
