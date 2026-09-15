#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集与仪表盘序列化器。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.dataset import Dashboard, Dataset
from system.utils.dataset import filter_layout_for_user, validate_dataset

ALLOWED_CHART_TYPES = ("number", "line", "bar", "pie")


class DatasetSerializer(BaseModelSerializer):
    # 定义类资源（配置对象）不做字段权限裁剪：可见性语义 = 创建者/共享；
    # 字段权限叠加发生在执行/聚合输出侧（system/utils/dataset.py，ADR-042 二期）
    ignore_field_permission = True

    class Meta:
        model = Dataset
        fields = [
            "pk",
            "name",
            "description",
            "bound_model",
            "columns",
            "filters",
            "ordering",
            "row_limit",
            "config",
            "visibility",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "created_time", "updated_time"]
        # RePlusPage 列表列：列/filters/ordering/config 等定义细节不进列表
        table_fields = ["name", "bound_model", "visibility", "description", "updated_time"]

    def validate(self, attrs):
        """保存侧白名单校验：部分更新时与既有实例字段合并后整体校验。"""
        merged = {
            "bound_model": attrs.get("bound_model", getattr(self.instance, "bound_model", "")),
            "columns": attrs.get("columns", getattr(self.instance, "columns", [])),
            "filters": attrs.get("filters", getattr(self.instance, "filters", [])),
            "ordering": attrs.get("ordering", getattr(self.instance, "ordering", "")),
            "row_limit": attrs.get("row_limit", getattr(self.instance, "row_limit", 1000)),
            "config": attrs.get("config", getattr(self.instance, "config", {})),
        }
        validate_dataset(Dataset(**merged))
        return attrs


class DashboardSerializer(BaseModelSerializer):
    # 同 DatasetSerializer：定义类资源豁免字段权限
    ignore_field_permission = True

    class Meta:
        model = Dashboard
        fields = ["pk", "name", "layout", "visibility", "created_time", "updated_time"]
        read_only_fields = ["pk", "created_time", "updated_time"]

    def validate_layout(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid dashboard layout"))
        dataset_pks = {str(pk) for pk in Dataset.objects.values_list("pk", flat=True)}
        from system.models import UserRole

        known_role_codes = set(UserRole.objects.values_list("code", flat=True))
        for card in value or []:
            if not isinstance(card, dict) or not card.get("id") or not card.get("dataset"):
                raise serializers.ValidationError(_("Invalid dashboard layout"))
            if card.get("chart_type", "number") not in ALLOWED_CHART_TYPES:
                raise serializers.ValidationError(_("Invalid chart type: {}").format(card.get("chart_type")))
            if str(card["dataset"]) not in dataset_pks:
                raise serializers.ValidationError(_("Unknown dataset in layout"))
            # 卡片级权限（allowed_roles）：空 = 全员可见；非空须为已知角色 code
            allowed_roles = card.get("allowed_roles") or []
            if not isinstance(allowed_roles, list) or any(not isinstance(item, str) for item in allowed_roles):
                raise serializers.ValidationError(_("Invalid card roles"))
            unknown = [code for code in allowed_roles if code not in known_role_codes]
            if unknown:
                raise serializers.ValidationError(_("Unknown role codes: {}").format(", ".join(unknown)))
        return value

    def to_representation(self, instance):
        """读取侧按浏览者过滤卡片（卡片级权限；超管全量，匿名 fail-closed）。"""
        data = super().to_representation(instance)
        request = self.context.get("request")
        data["layout"] = filter_layout_for_user(instance.layout, getattr(request, "user", None))
        return data
