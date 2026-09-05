#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""元数据 Action：choices 聚合 / search-fields / search-columns。

前端 RePlusPage 注册表渲染依赖的三大元数据接口。拆分自 modelset.py（T2.1）。
"""

import json
from typing import Callable

from django.forms.widgets import DateTimeInput, SelectMultiple
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
from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)


class ChoicesAction(object):
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


class SearchFieldsAction(object):
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
        try:
            filterset_class = self.filterset_class.get_filters()
            filter_fields = self.filterset_class.get_fields().keys()
            for field_name, value in filterset_class.items():
                if field_name not in filter_fields:
                    continue
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
                # PERF-07：关联字段的 widget.choices 同样会全量求值，这里做同样的行数上限
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
            logger.error(f"get search-field failed {e}")
        return ApiResponse(data=results)


class SearchColumnsAction(object):
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
                setattr(value.child_relation, "is_column", True)
                choices_owner = value.child_relation
                tp = get_format_intput_type(value.child_relation, info["type"])
            else:
                tp = get_format_intput_type(value, info["type"])
                choices_owner = value
            if tp and tp.endswith("related_field"):
                setattr(value, "is_column", True)
                # PERF-07：超上限时仅返回前 SEARCH_CHOICES_MAX_COUNT 条，并带出截断标记供前端降级
                info["choices"] = json.loads(json.dumps(value.choices, cls=encoders.JSONEncoder))
                if getattr(choices_owner, "choices_truncated", False):
                    info["choices_truncated"] = True
            return tp

        metadata_class = self.metadata_class()
        serializer = self.get_serializer()
        fields = getattr(serializer, "fields", [])
        meta = getattr(serializer, "Meta", {})
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

            if value.field_name.replace("_", " ").capitalize() == info["label"] and hasattr(field, "verbose_name"):
                info["label"] = field.verbose_name

            if isinstance(value, CharField) and value.style.get("base_template", "") == "textarea.html":
                info["input_type"] = "textarea"
            else:
                info["input_type"] = get_input_type(value, info)
            del info["type"]
            if not table_fields:
                info["table_show"] = 1
            if key in table_fields:
                info["table_show"] = (table_fields.index(key)) + 1
            if tabs_info and tabs_label:
                info["tabs_index"] = tabs_info.get(key, 0)
                info["tabs_label"] = tabs_label[info["tabs_index"]]
            results.append(info)
        return ApiResponse(data=results)
