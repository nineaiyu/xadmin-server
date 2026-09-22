#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批规则序列化器（多级审批链配置）。

- ApprovalRuleSerializer：规则 + 级次列表嵌套写入（levels 整体替换式更新）；
  保存时校验路径正则可编译、级次审批人存在（用户名 / 角色 code），
  避免「配错人名」拖到建单（拦截发生）时才发现；
- 规则改动只影响之后新建的审批单：在途单的级次是建单瞬间的快照，不受影响。
"""

import re

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.approval_rule import ApprovalRule, ApprovalRuleLevel


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
            "priority",
            "is_active",
            "remark",
            "levels",
            "level_count",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk"]
        table_fields = ["name", "path_patterns", "level_count", "priority", "is_active", "remark", "created_time"]

    def get_level_count(self, obj) -> int:
        annotated = getattr(obj, "levels_count", None)
        return annotated if annotated is not None else obj.levels.count()

    def validate_path_patterns(self, value):
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

    def validate_levels(self, value):
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

    def _validate_assignee(self, level):
        values = [value.strip() for value in str(level.get("assignee_value") or "").split(",") if value.strip()]
        if not values:
            raise serializers.ValidationError(
                _("Level {order} requires at least one approver").format(order=level.get("order"))
            )
        if level.get("assignee_type") == ApprovalRuleLevel.AssigneeType.ROLE:
            from system.models import UserRole

            existing = set(
                UserRole.objects.filter(code__in=values, deleted_at__isnull=True).values_list("code", flat=True)
            )
            missing = [value for value in values if value not in existing]
            if missing:
                raise serializers.ValidationError(_("Role does not exist: {}").format(", ".join(missing)))
            return
        from system.models import UserInfo

        existing = set(UserInfo.objects.filter(username__in=values).values_list("username", flat=True))
        missing = [value for value in values if value not in existing]
        if missing:
            raise serializers.ValidationError(_("User does not exist: {}").format(", ".join(missing)))

    @transaction.atomic
    def create(self, validated_data):
        levels = validated_data.pop("levels", [])
        rule = super().create(validated_data)
        self._replace_levels(rule, levels)
        return rule

    @transaction.atomic
    def update(self, instance, validated_data):
        levels = validated_data.pop("levels", None)
        rule = super().update(instance, validated_data)
        if levels is not None:
            rule.levels.all().delete()
            self._replace_levels(rule, levels)
        return rule

    def _replace_levels(self, rule, levels):
        for level in levels:
            level.pop("pk", None)
            ApprovalRuleLevel.objects.create(rule=rule, **level)
