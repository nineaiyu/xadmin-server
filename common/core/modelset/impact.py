#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""影响面预检 Action：``POST {resource}/impact`` ``{"pks": [...]}``。

视图混入本 Action 后即获得「删除/停用前的影响面预览」端点（读类、数据权限内过滤）：
返回逐对象明细（引用方计数 + 样本 + 处置建议）与批量汇总（totals / has_impact），
前端在删除确认前调用并在弹窗展示（见 RePlusPage 的 impact 工具）。

未混入的视图不生成该路由（前端探测 404 后静默跳过，零侵入）。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.services import guarded_models, impact_for_many

#: 单次预检的主键数上限（防大 payload 打爆计算器）
IMPACT_MAX_ITEMS = 200


class ImpactPreviewAction:
    """影响面预检：删除 / 批量删除 / 停用前的影响范围与引用方清单。"""

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR))})
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="impact")
    def impact(self, request, *args, **kwargs):
        """获取{cls}的影响面预览"""
        pks = request.data.get("pks") if isinstance(request.data, dict) else request.data
        if not isinstance(pks, (list, tuple)) or not pks:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        if len(pks) > IMPACT_MAX_ITEMS:
            return ApiResponse(code=1004, detail=_("Too many items to preview (max {})").format(IMPACT_MAX_ITEMS))
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        data = impact_for_many(queryset)
        model = getattr(getattr(self, "queryset", None), "model", None)
        data["guarded"] = bool(model is not None and model._meta.label_lower in guarded_models())
        return ApiResponse(data=data)
