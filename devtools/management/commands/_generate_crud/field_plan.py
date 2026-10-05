#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：字段规划（序列化器字段 / 表格列 / 过滤字段映射）。"""

from django.conf import settings
from django.db import models

from .constants import AUDIT_FIELDS, FILE_RELATED_MODEL, SEARCH_EXCLUDE_TYPES


class FieldPlanMixin:
    """字段映射规则：从模型推导序列化器字段、表格列与过滤字段。"""

    @staticmethod
    def _default_ordering(model) -> str:
        """列表视图默认排序（空串表示无需生成）。

        门禁 tests/unit/system/test_viewset_ordering.py 要求列表 ViewSet 声明
        ``ordering`` 或模型 ``Meta.ordering`` 非空（``ordering_fields`` 只放开
        ``?ordering=`` 参数）；模型未声明时生成 ``created_time`` 倒序（项目基类
        字段），无该字段退回 pk 倒序——保证「生成即过门禁」对任意模型成立。
        """
        if getattr(model._meta, "ordering", None):
            return ""
        has_created_time = any(field.name == "created_time" for field in model._meta.fields)
        return "-created_time" if has_created_time else "-pk"

    @staticmethod
    def _snake(name):
        out = []
        for index, char in enumerate(name):
            if char.isupper() and index and not name[index - 1].isupper():
                out.append("_")
            out.append(char.lower())
        return "".join(out)

    def _field_plan(self, model):
        """字段映射规则：序列化器字段 / 表格列 / extra_kwargs / 搜索字段。"""
        serializer_fields = ["pk"]
        for field in model._meta.fields:
            if not field.primary_key and field.name not in AUDIT_FIELDS:
                serializer_fields.append(field.name)
        for field in model._meta.many_to_many:
            serializer_fields.append(field.name)

        extra_kwargs = {"pk": {"read_only": True}}
        for field in self._forward_relations(model):
            if field.name in AUDIT_FIELDS:
                continue
            extra_kwargs[field.name] = self._relation_kwargs(field)

        return {
            "serializer_fields": serializer_fields,
            "table_fields": self._table_fields(model),
            "extra_kwargs": extra_kwargs,
            "filter_custom_fields": self._filter_custom_fields(model),
            "filter_meta_fields": self._filter_meta_fields(model),
        }

    @staticmethod
    def _forward_relations(model):
        relations = []
        for field in model._meta.get_fields():
            if not field.is_relation or field.auto_created:
                continue
            if field.many_to_many or field.many_to_one or field.one_to_one:
                relations.append(field)
        return relations

    @staticmethod
    def _relation_kwargs(field):
        related = field.related_model
        if related._meta.label_lower == settings.AUTH_USER_MODEL.lower():
            attrs, fmt, input_type = ["pk", "username"], "{username}({pk})", "api-search-user"
        elif any(item.name == "name" for item in related._meta.fields):
            attrs, fmt, input_type = ["pk", "name"], "{name}({pk})", None
        else:
            attrs, fmt, input_type = ["pk"], "{pk}", None
        kwargs: dict = {"attrs": attrs, "format": fmt}
        if input_type:
            kwargs["input_type"] = input_type
        if field.many_to_many:
            kwargs["required"] = False
        else:
            kwargs["required"] = not field.null and not field.blank
        return kwargs

    @staticmethod
    def _table_fields(model):
        """表格列：关联/choices/布尔/短文本优先，最多 8 列（长文本、JSON、文件不入列）。"""
        buckets: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS or field.name in ("created_time", "updated_time"):
                continue
            if isinstance(field, SEARCH_EXCLUDE_TYPES) or isinstance(field, models.TextField):
                continue
            if field.is_relation:
                buckets[1].append(field.name)
            elif field.choices:
                buckets[2].append(field.name)
            elif isinstance(field, models.BooleanField):
                buckets[3].append(field.name)
            elif isinstance(field, models.CharField) and field.max_length <= 128:
                buckets[4].append(field.name)
        for field in model._meta.many_to_many:
            if field.related_model._meta.label_lower != FILE_RELATED_MODEL:
                buckets[1].append(field.name)
        ordered = [name for index in (1, 2, 3, 4) for name in buckets[index]]
        return ["pk"] + ordered[:8]

    @staticmethod
    def _filter_custom_fields(model):
        """搜索自定义过滤器：非 choices 的文本字段走 icontains。"""
        names = []
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS or field.choices:
                continue
            if isinstance(field, (models.CharField, models.TextField, models.EmailField, models.SlugField)):
                names.append(field.name)
        return names

    @staticmethod
    def _filter_meta_fields(model):
        """搜索表单字段域：文本/choices/布尔/日期/关联；排除大字段与文件关联。"""
        names = []
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS:
                continue
            if isinstance(field, SEARCH_EXCLUDE_TYPES):
                continue
            if field.is_relation and field.related_model._meta.label_lower == FILE_RELATED_MODEL:
                continue
            names.append(field.name)
        for field in model._meta.many_to_many:
            if field.related_model._meta.label_lower != FILE_RELATED_MODEL:
                names.append(field.name)
        return names
