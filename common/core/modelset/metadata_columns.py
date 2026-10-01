#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""列表列元数据（search-columns）：列定义 / 渲染器 / 详情渲染下发（自 metadata.py 拆分，行为不变）。"""

import json
from typing import TYPE_CHECKING, Any

from django_filters.utils import get_model_field
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.fields import CharField
from rest_framework.utils import encoders

from common.core.modelset.input_types import get_format_intput_type
from common.core.modelset.metadata import (
    METADATA_CACHE_TIMEOUT,
    metadata_cache_bypass,
    metadata_cache_key,
)
from common.core.modelset.metadata_cache import cached_payload
from common.core.modelset.suggest import expose_suggest_url
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)


class SearchColumnsAction:
    filterset_class: Any

    if TYPE_CHECKING:  # 宿主 ViewSet / DRF 提供的接口（mixin 模式）
        metadata_class: Any

        def get_serializer(self) -> Any: ...

        def get_ordering_fields(self, request) -> Any: ...

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "key": build_basic_type(OpenApiTypes.STR),
                            "label": build_basic_type(OpenApiTypes.STR),
                            "help_text": build_basic_type(OpenApiTypes.STR),
                            "default": build_basic_type(OpenApiTypes.ANY),
                            "input_type": build_basic_type(OpenApiTypes.STR),
                            "required": build_basic_type(OpenApiTypes.BOOL),
                            "read_only": build_basic_type(OpenApiTypes.BOOL),
                            "write_only": build_basic_type(OpenApiTypes.BOOL),
                            "multiple": build_basic_type(OpenApiTypes.BOOL),
                            "max_length": build_basic_type(OpenApiTypes.NUMBER),
                            "table_show": build_basic_type(OpenApiTypes.NUMBER),
                            "choices": build_array_type(
                                build_object_type(
                                    properties={
                                        "pk": build_basic_type(OpenApiTypes.STR),
                                        "value": build_basic_type(OpenApiTypes.STR),
                                        "label": build_basic_type(OpenApiTypes.STR),
                                    }
                                )
                            ),
                            "choices_truncated": build_basic_type(OpenApiTypes.BOOL),
                            "sortable": build_basic_type(OpenApiTypes.BOOL),
                            "lookups": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                        }
                    )
                )
            }
        )
    )
    @shared_list_action(methods=["get"], detail=False, url_path="search-columns")
    def search_columns(self, request, *args, **kwargs):
        """获取{cls}的展示字段"""
        cache_key = metadata_cache_key(self, "search_columns", request)
        cache_bypass = metadata_cache_bypass(request)
        results = cached_payload(
            cache_key,
            METADATA_CACHE_TIMEOUT,
            lambda: self._build_search_columns(request),
            bypass=cache_bypass,
        )
        return ApiResponse(data=results)

    def _build_search_columns(self, request):
        """构建展示字段元数据（request 供联想地址 / 排序声明解析）。"""
        results = []

        def get_input_type(value, info):
            if hasattr(value, "child_relation") and isinstance(value.child_relation, BasePrimaryKeyRelatedField):
                info["multiple"] = True
                value.child_relation.is_column = True
                choices_owner = value.child_relation
                tp = get_format_intput_type(value.child_relation, info["type"])
            else:
                tp = get_format_intput_type(value, info["type"])
                choices_owner = value
            if tp and tp.endswith("related_field"):
                value.is_column = True
                # 超上限时仅返回前 SEARCH_CHOICES_MAX_COUNT 条，并带出截断标记供前端降级
                info["choices"] = json.loads(json.dumps(value.choices, cls=encoders.JSONEncoder))
                if getattr(choices_owner, "choices_truncated", False):
                    info["choices_truncated"] = True
            return tp

        metadata_class = self.metadata_class()
        serializer = self.get_serializer()
        fields = getattr(serializer, "fields", None) or {}
        meta = getattr(serializer, "Meta", {})

        # 表头排序声明面：与 DRF OrderingFilter 的 ordering_fields 同源，
        # `-` 前缀仅表默认方向、不影响该字段可排序；"__all__" 视为全部字段可排序。
        # 未声明 ordering_fields 的视图集不下发 sortable —— 前端表头保持不可排序（零变化）。
        ordering_fields = getattr(self, "ordering_fields", None)
        if ordering_fields is None and callable(getattr(self, "get_ordering_fields", None)):
            try:
                ordering_fields = self.get_ordering_fields(request)
            except Exception as e:
                logger.error(f"get ordering fields failed {e}")
                ordering_fields = None
        if ordering_fields == "__all__":
            sortable_names = None
        elif isinstance(ordering_fields, str):
            sortable_names = {ordering_fields.lstrip("-")}
        else:
            sortable_names = {str(name).lstrip("-") for name in (ordering_fields or [])}

        table_fields = getattr(meta, "table_fields", [])
        tabs_fields = getattr(meta, "tabs", [])
        tabs_label = []
        tabs_info = {}
        if tabs_fields:
            index = 0
            for tabs in tabs_fields:
                tabs_label.append(tabs.label)
                for field in tabs.fields:
                    tabs_info[field] = index
                index += 1

        for key, value in fields.items():
            info = metadata_class.get_field_info(value)
            if hasattr(meta, "model"):
                field = get_model_field(meta.model, value.source)
            else:
                field = None
            info["key"] = key
            if info.get("help_text", None) is None and hasattr(field, "help_text"):
                info["help_text"] = field.help_text

            # 表头排序标记：按序列化器字段名 / source 命中 ordering_fields 声明面
            source_name = getattr(value, "source", None)
            if (
                sortable_names is None
                or key in sortable_names
                or (isinstance(source_name, str) and source_name in sortable_names)
            ):
                info["sortable"] = True

            if value.field_name.replace("_", " ").capitalize() == info["label"] and hasattr(field, "verbose_name"):
                info["label"] = field.verbose_name

            if isinstance(value, CharField) and value.style.get("base_template", "") == "textarea.html":
                info["input_type"] = "textarea"
            else:
                info["input_type"] = get_input_type(value, info)
            # 混入 SuggestionsAction 的视图，对 api-search-* 关联字段下发联想地址
            expose_suggest_url(self, request, info, info["input_type"])
            del info["type"]
            if not table_fields:
                info["table_show"] = 1
            if key in table_fields:
                info["table_show"] = (table_fields.index(key)) + 1
            if tabs_info and tabs_label:
                info["tabs_index"] = tabs_info.get(key, 0)
                info["tabs_label"] = tabs_label[info["tabs_index"]]
            # 受控 lookup：开启 controlled_lookup 的视图，为白名单字段下发可用表达式，
            # 前端高级筛选据此渲染字段候选与操作符（与后端白名单同源，避免「选到即 400」）
            if getattr(self, "controlled_lookup", False):
                from common.core.filter import ControlledLookupFilterBackend

                # 序列化器 source 与 filterset 字段名可能不一致（主键字段 pk 的 source 为 id / 空）：
                # 依次按 source、key 解析模型字段与白名单，首个命中即下发
                candidates = []
                source_name = value.source if isinstance(value.source, str) else None
                if source_name:
                    candidates.append(source_name)
                if key not in candidates:
                    candidates.append(key)
                model_cls = getattr(meta, "model", None)
                for name in candidates:
                    model_field = (
                        ControlledLookupFilterBackend._model_field(model_cls, name) if model_cls is not None else None
                    )
                    lookups = ControlledLookupFilterBackend.field_lookups(self, model_field, name)
                    if lookups:
                        info["lookups"] = lookups
                        break
            results.append(info)
        return results
