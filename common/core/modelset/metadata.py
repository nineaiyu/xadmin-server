#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""元数据 Action：choices 聚合 / search-fields / search-columns。

前端 RePlusPage 注册表渲染依赖的三大元数据接口。拆分自 modelset.py。
"""

import json
from collections.abc import Callable

from django.forms.widgets import DateTimeInput, SelectMultiple
from django.utils.translation import gettext_lazy as _
from django_filters.utils import get_model_field
from django_filters.widgets import DateRangeWidget
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.fields import CharField
from rest_framework.utils import encoders

from common.base.utils import get_choices_dict
from common.core.fields import get_search_choices_max_count
from common.core.modelset.input_types import get_format_intput_type
from common.core.modelset.suggest import expose_suggest_url
from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)


class ChoicesAction:
    choices_models: []

    @extend_schema(
        responses=get_default_response_schema(
            {
                "choices_dict": build_object_type(
                    properties={
                        "key": build_array_type(
                            build_object_type(
                                properties={
                                    "value": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="choices")
    def choices_dict(self, request, *args, **kwargs):
        """获取{cls}的字段选择"""
        result = {}
        models = getattr(self, "choices_models", None)
        if not models:
            models = [self.queryset.model]
        for model in models:
            for field in model._meta.fields:
                choices = field.choices
                if choices:
                    result[field.name] = get_choices_dict(choices)
        return ApiResponse(choices_dict=result)


class SearchFieldsAction:
    filterset_class: Callable

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
                        }
                    )
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="search-fields")
    def search_fields(self, request, *args, **kwargs):
        """获取{cls}的查询字段"""
        results = []
        if getattr(self, "filterset_class", None) is None:
            # 非模型视图集（内存 queryset / 未声明 filterset，如 IP 拦截名单）：
            # 「没有可筛选字段」是正常语义，返回空元数据；真正的构建异常仍走下面失败码
            return ApiResponse(data=[])
        try:
            filterset_class = self.filterset_class.get_filters()
            filter_fields = self.filterset_class.get_fields().keys()
        except Exception as e:
            # 整体无法构建（filterset 配置异常等）：返回失败码，
            # 而不是 HTTP 200 + code=1000 的"成功但残缺"元数据让前端静默降级
            logger.error(f"get search-field failed {e}")
            return ApiResponse(code=500, detail=_("Failed to get search fields"))
        for field_name, value in filterset_class.items():
            if field_name not in filter_fields:
                continue
            # 单字段异常只跳过该字段并记录，不牵连其余字段的元数据
            try:
                widget = value.field.widget
                if isinstance(widget, SelectMultiple):
                    widget.input_type = "select-multiple"
                if isinstance(widget, DateRangeWidget):
                    widget.input_type = "datetimerange"
                if isinstance(widget, DateTimeInput):
                    widget.input_type = "datetime"
                # if hasattr(value.field, 'queryset'):  # 将一些具有关联的字段的数据置空
                #     widget.input_type = 'text'
                #     widget.choices = []
                widget.input_type = get_format_intput_type(value, widget.input_type)
                choices = list(getattr(widget, "choices", []))
                if choices and len(choices) > 0 and choices[0][0] == "":
                    choices.pop(0)
                # 关联字段的 widget.choices 同样会全量求值，这里做同样的行数上限
                max_choices = get_search_choices_max_count()
                choices_truncated = False
                if max_choices and len(choices) > max_choices:
                    choices = choices[:max_choices]
                    choices_truncated = True
                field = get_model_field(self.filterset_class._meta.model, value.field_name)
                results.append(
                    {
                        "key": field_name,
                        "label": value.label
                        if value.label
                        else (getattr(field, "verbose_name", field.name) if field else field_name),
                        "help_text": value.field.help_text
                        if value.field.help_text
                        else getattr(field, "help_text", None),
                        "input_type": widget.input_type,
                        "choices": get_choices_dict(choices),
                        "default": [] if "multiple" in widget.input_type else "",
                        **({"choices_truncated": True} if choices_truncated else {}),
                    }
                )
            except Exception as e:
                logger.error(f"get search-field failed. field:{field_name} error:{e}")
                continue
        try:
            order_choices = []
            ordering_fields = list(getattr(self, "ordering_fields", []))
            for choice in ordering_fields:
                is_des = False
                if choice.startswith("-"):
                    choice = choice[1:]
                    is_des = True
                label = choice
                field = get_model_field(self.filterset_class._meta.model, choice)
                if field:
                    label = getattr(field, "verbose_name", choice)
                des = (f"-{choice}", f"{label} descending")
                ase = (choice, f"{label} ascending")
                if is_des:
                    des, ase = ase, des
                order_choices.extend([des, ase])
            if order_choices:
                results.append(
                    {
                        "label": "ordering",
                        "key": "ordering",
                        "input_type": "select-ordering",
                        "choices": get_choices_dict(order_choices),
                        "default": order_choices[0][0],
                    }
                )
        except Exception as e:
            # ordering 段失败不影响已收集的字段元数据
            logger.error(f"get search-field ordering failed {e}")
        return ApiResponse(data=results)


class SearchColumnsAction:
    filterset_class: Callable

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
                            "lookups": build_array_type(build_basic_type(OpenApiTypes.STR)),
                        }
                    )
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="search-columns")
    def search_columns(self, request, *args, **kwargs):
        """获取{cls}的展示字段"""
        results = []

        # def check_upload_tp(value, tp):
        #     if hasattr(value, 'child_relation'):
        #         value = value.child_relation
        #     try:
        #         if (value.queryset.model._meta.label == "system.UploadFile"
        #                 and isinstance(value, BasePrimaryKeyRelatedField)
        #                 and tp in ['object_related_field', 'm2m_related_field']):
        #             return tp + "_file"
        #     except Exception:
        #         pass
        #     return tp

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
        fields = getattr(serializer, "fields", [])
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
        return ApiResponse(data=results)
