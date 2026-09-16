#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""远程联想 Action：`GET {prefix}/suggestions?field=&search=&pks=&limit=`。

设计要点：

- **引用方挂载**：混入到引用关联字段的 ViewSet 并声明 ``suggestion_fields``
  字段白名单，权限链回落到该资源的 list 权限点
  （`common/core/permission.py::_resolve_menu_pk` 的 `suggestions$` 特例），
  零新权限点、零新增枚举面；
- **候选集与写入同源**：直接取 serializer 字段（`BasePrimaryKeyRelatedField` 及其
  many 的 `child_relation`）的 `get_queryset()`——该口径已含数据权限过滤
  （`get_filter_queryset`），与 `to_internal_value` 用同一 queryset，
  杜绝「下拉能选、提交报 does_not_exist」的口径分叉；
- **label 与展示同源**：复用字段声明的 `attrs` + `format`（`to_representation`），
  与列表/详情/表格展示口径一致，且同样经过字段权限裁剪；
- **防滥用**：`search` 与 `pks` 至少给一个，否则返回空（禁止当全量导出接口用）；
  `limit` 硬上限 50；`pks` 数量截断到上限内。

元数据暴露：ViewSet 混入本 Action 后，`search-columns` 对 `input_type="api-search-*"`
的关联字段附加 `suggest_url`（见 metadata.py）；前端渲染器据此把「弹窗选择」升级为
「输入联想」，未混入的 ViewSet 行为零变化。
"""

import re

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)

SUGGEST_LIMIT_DEFAULT = 20
SUGGEST_LIMIT_MAX = 50


class SuggestionsAction:
    """远程联想：`{prefix}/suggestions?field=<related field>&search=<keyword>`。

    ViewSet 可选属性：
    - ``suggestion_fields``: 联想字段名白名单（集合/列表）。**声明式收敛暴露面**——
      仅名单内的关联字段下发 ``suggest_url`` 并接受 ``field`` 参数；未声明（默认空）
      时本 Action 不产生任何行为。同一表单里可以只让部分关联字段走联想
      （如审批委托：代理人走联想、委托人保持弹窗选择器）。
    - ``suggestion_search_fields``: ``{field_name: [model lookup, ...]}``，
      覆盖按字段 ``attrs`` 自动推导的搜索字段（自动推导取 attrs 中能解析到
      具体模型字段的项，跨级 ``__`` lookup 保留）。
    """

    suggestion_search_fields: dict = {}
    suggestion_fields: tuple = ()

    @extend_schema(
        parameters=[],
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "value": build_basic_type(OpenApiTypes.STR),
                            "label": build_basic_type(OpenApiTypes.STR),
                            "pk": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["get"], detail=False, url_path="suggestions")
    def suggestions(self, request, *args, **kwargs):
        """获取{cls}关联字段的联想候选（候选集与写入校验同源）"""
        field_name = (request.query_params.get("field") or "").strip()
        search = (request.query_params.get("search") or "").strip()
        pks = [pk for pk in (request.query_params.get("pks") or "").split(",") if pk]
        try:
            limit = int(request.query_params.get("limit") or SUGGEST_LIMIT_DEFAULT)
        except (TypeError, ValueError):
            limit = SUGGEST_LIMIT_DEFAULT
        limit = max(1, min(limit, SUGGEST_LIMIT_MAX))

        if not field_name:
            return ApiResponse(code=1001, detail=_("Missing field parameter"))
        # 字段白名单：名单外的关联字段一律拒绝（与 suggest_url 下发同一份声明，
        # 防止绕过元数据直接枚举本资源的其它关联字段）。
        if field_name not in (getattr(self, "suggestion_fields", None) or ()):
            return ApiResponse(code=1001, detail=_("Field is not a related field"))
        # 字段白名单取「类型定义」而非权限裁剪后的实例：非超管在无字段权限配置时
        # get_serializer() 会把 fields 全裁（空），导致合法关联字段被误判 1001。
        # ignore_field_permission=True 只放宽本实例的白名单判定，候选输出仍按
        # request.fields 逐字段裁剪（BasePrimaryKeyRelatedField.get_allow_fields
        # 独立读取 request 级标志，与实例级豁免互不影响）。
        serializer = self.get_serializer_class()(context=self.get_serializer_context(), ignore_field_permission=True)
        field = serializer.fields.get(field_name)
        if getattr(field, "child_relation", None) is not None:
            field = field.child_relation
        if not isinstance(field, BasePrimaryKeyRelatedField):
            return ApiResponse(code=1001, detail=_("Field is not a related field"))

        queryset = field.get_queryset()
        if queryset is None:
            return ApiResponse(code=1001, detail=_("Field is not a related field"))

        if pks:
            # 回显：只补已选主键的 label，数量按上限截断防止拼长列表
            queryset = queryset.filter(pk__in=pks[:SUGGEST_LIMIT_MAX])
        elif search:
            query = Q()
            for lookup in self._resolve_search_fields(field_name, field, queryset.model):
                query |= Q(**{f"{lookup}__icontains": search})
            queryset = queryset.filter(query) if query else queryset.none()
        else:
            # 无 search 无 pks：不做全量下发（防枚举/防当导出接口用）
            return ApiResponse(data=[])

        results = []
        for item in queryset[:limit]:
            data = field.to_representation(item)
            if not isinstance(data, dict):
                data = {"pk": data, "label": data}
            data.setdefault("value", data.get("pk"))
            results.append(data)
        return ApiResponse(data=results)

    def _resolve_search_fields(self, field_name, field, model):
        """解析联想搜索字段：ViewSet 显式配置优先，否则按字段 attrs 推导。"""
        configured = getattr(self, "suggestion_search_fields", None) or {}
        if field_name in configured:
            return list(configured[field_name])
        fields = []
        for attr in getattr(field, "attrs", None) or []:
            if attr in ("pk", "id"):
                continue
            try:
                model_field = model._meta.get_field(attr.split("__")[0])
            except Exception:
                # attrs 里可能声明展示辅助（如 get_xxx_display），不是模型字段，跳过
                continue
            is_cross_level = "__" in attr
            if (getattr(model_field, "concrete", False) and not model_field.is_relation) or is_cross_level:
                fields.append(attr)
        return fields

    @staticmethod
    def get_suggest_url(request):
        """由当前请求路径推导同资源的 `suggestions` 地址。

        两种调用形态（仅在混入了本 Action 的 ViewSet 上被 metadata.py 调用）：
        - 独立元数据接口：path 为 `{prefix}/search-columns`，剥离后缀；
        - with_meta=1 内联元数据：list 响应内联调用 search_columns，此时
          path 就是资源前缀 `{prefix}` 本身，直接拼接。
        """
        base = re.sub(r"/search-columns$", "", request.path_info)
        if not base:
            return None
        return f"{base}/suggestions"


def expose_suggest_url(view, request, info, input_type):
    """metadata 侧挂钩：按 ViewSet 声明的 ``suggestion_fields`` 白名单下发 suggest_url。

    在 search-columns 组装字段元数据时调用；只有混入 SuggestionsAction 且声明了
    白名单、字段命中名单（且 input_type 为 api-search-*）才下发。未混入或未声明
    的视图零变化。
    """
    if not isinstance(input_type, str) or not input_type.startswith("api-search-"):
        return
    allowed = getattr(view, "suggestion_fields", None)
    if not allowed or info.get("key") not in allowed:
        return
    get_suggest_url = getattr(view, "get_suggest_url", None)
    if get_suggest_url is None:
        return
    suggest_url = get_suggest_url(request)
    if suggest_url:
        info["suggest_url"] = suggest_url
