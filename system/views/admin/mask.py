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

    @extend_schema(description="脱敏预览：传入样例值与规则参数，返回脱敏结果（供前端表单实时预览）")
    @action(methods=["post"], detail=False)
    def preview(self, request, *args, **kwargs):
        """给定样例值与规则参数，返回脱敏结果（供前端表单实时预览）。"""
        value = request.data.get("value", "")
        rule = request.data.get("rule")
        if not isinstance(rule, dict):
            raise ValidationError(_("Invalid mask rule"))
        if rule.get("mask_type") and rule["mask_type"] not in DataMaskRule.MaskType.values:
            raise ValidationError(_("Invalid mask type"))
        return ApiResponse(data={"result": apply_mask(str(value) if value is not None else "", rule)})
