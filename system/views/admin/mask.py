#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏规则管理（蒙版规则增删改查 + 实时预览）。"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.response import ApiResponse
from common.utils import get_logger
from system.models.mask import DataMaskRule
from system.serializers.mask import DataMaskRuleSerializer
from system.utils.mask import apply_mask

logger = get_logger(__name__)

# 预览批量上限：规则可能含自定义正则，批量样例需限制单次计算量
PREVIEW_MAX_VALUES = 20
PREVIEW_MAX_VALUE_LENGTH = 500


def collect_preview_values(payload):
    """归一化预览样例：兼容 ``values`` 数组与 ``value`` 换行多值两种入参。

    返回 ``(values, truncated)``：样例按顺序去空白行、逐条限长、整体限量，
    任一维度被截断时 ``truncated=True``（前端据此提示只预览了部分样例）。
    """
    raw_values = payload.get("values")
    if raw_values is not None:
        if not isinstance(raw_values, (list, tuple)):
            raise ValidationError(_("Invalid sample values"))
        items = ["" if item is None else str(item) for item in raw_values]
    else:
        value = payload.get("value", "")
        text = "" if value is None else str(value)
        # 换行即多值：样例框内一次粘贴多条（尾随换行不产生空样例）
        items = [line for line in text.splitlines() if line.strip()] if "\n" in text else [text]
    truncated = len(items) > PREVIEW_MAX_VALUES
    normalized = []
    for item in items[:PREVIEW_MAX_VALUES]:
        if len(item) > PREVIEW_MAX_VALUE_LENGTH:
            truncated = True
            item = item[:PREVIEW_MAX_VALUE_LENGTH]
        normalized.append(item)
    return normalized, truncated


class DataMaskRuleFilter(BaseFilterSet):
    model = filters.CharFilter(field_name="model", lookup_expr="icontains")
    field = filters.CharFilter(field_name="field", lookup_expr="icontains")

    class Meta:
        model = DataMaskRule
        fields = ["model", "field", "mask_type", "is_active"]


class DataMaskRuleViewSet(BaseModelSet, ImportExportDataAction):
    """脱敏规则"""

    queryset = DataMaskRule.objects.all()
    serializer_class = DataMaskRuleSerializer
    filterset_class = DataMaskRuleFilter
    ordering_fields = ["sort", "created_time"]

    @extend_schema(description="脱敏预览：传入样例值与规则参数，返回逐条脱敏结果（供前端表单实时预览）")
    @action(methods=["post"], detail=False)
    def preview(self, request, *args, **kwargs):
        """给定样例值与规则参数，返回脱敏结果（供前端表单实时预览）。

        样例可为 ``values`` 数组，或 ``value`` 内含换行（按行拆分）；``result``
        保留首条结果以兼容单值调用方，逐条结果统一在 ``results``。
        """
        rule = request.data.get("rule")
        if not isinstance(rule, dict):
            raise ValidationError(_("Invalid mask rule"))
        if rule.get("mask_type") and rule["mask_type"] not in DataMaskRule.MaskType.values:
            raise ValidationError(_("Invalid mask type"))
        values, truncated = collect_preview_values(request.data)
        results = [{"input": value, "output": apply_mask(value, rule)} for value in values]
        return ApiResponse(
            data={
                "result": results[0]["output"] if results else "",
                "results": results,
                "truncated": truncated,
            }
        )
