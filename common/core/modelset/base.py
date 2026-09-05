#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""BaseViewSet：所有 ViewSet 的公共基类。

查询集优化（select_related / prefetch_related 自动推断）、action 级 serializer
选择、文件导出绕过分页。拆分自 modelset.py（T2.1），行为保持不变。
"""

from typing import Callable

from django.core.exceptions import FieldDoesNotExist
from django.db.models import QuerySet
from rest_framework import serializers

from common.utils import get_logger

logger = get_logger(__name__)


class BaseViewSet(object):
    action: Callable
    extra_filter_class = []
    # 查询优化：显式声明的关联字段，在所有 action 生效，支持 creator__dept 嵌套写法
    select_related_fields = ()
    prefetch_related_fields = ()
    # 是否根据 serializer 的关联字段自动推断 select_related / prefetch_related
    auto_prefetch_related = True
    # 自动推断仅在这些 action 生效（这些 action 会逐行序列化关联对象，存在 N+1 查询）
    auto_prefetch_actions = ("list", "retrieve", "export_data")

    def perform_destroy(self, instance):
        return instance.delete()

    def filter_queryset(self, queryset):
        for backend in set(set(self.filter_backends) | set(self.extra_filter_class or [])):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return self.optimize_queryset(queryset)

    def get_queryset(self):
        if getattr(self, "values_queryset", None):
            return self.values_queryset
        return super().get_queryset()

    def optimize_queryset(self, queryset):
        """
        为 queryset 应用 select_related / prefetch_related，消除列表/导出序列化时的 N+1 查询：
        - 显式声明的字段在所有 action 生效；
        - 自动推断的字段仅在 auto_prefetch_actions 中的 action 生效；
        - 非 QuerySet（如自定义视图返回的列表）直接透传。
        """
        auto_enabled = self.auto_prefetch_related and getattr(self, "action", None) in self.auto_prefetch_actions
        if not isinstance(queryset, QuerySet) or not (
            auto_enabled or self.select_related_fields or self.prefetch_related_fields
        ):
            return queryset
        select_fields = list(self.select_related_fields or ())
        prefetch_fields = list(self.prefetch_related_fields or ())
        if auto_enabled:
            auto_select, auto_prefetch = self.get_serializer_related_fields()
            select_fields += [field for field in auto_select if field not in select_fields]
            prefetch_fields += [field for field in auto_prefetch if field not in prefetch_fields]
        if select_fields:
            queryset = queryset.select_related(*select_fields)
        if prefetch_fields:
            queryset = queryset.prefetch_related(*prefetch_fields)
        return queryset

    def get_serializer_related_fields(self):
        """
        从当前 action 对应 serializer 的字段推断需要预取的关联字段：
        - ManyRelatedField（M2M/反向关联）序列化时每行都会执行 value.all()，需要 prefetch_related；
        - FK/OneToOne 的非 pk-only 关联字段（如 BasePrimaryKeyRelatedField）每行都会访问关联对象，
          需要 select_related；仅输出 pk 的字段走 DRF 的 attname 优化，不产生额外查询。
        推断结果按视图实例缓存（视图实例与请求同生命周期，字段权限在同一请求内不变）。
        """
        cached = getattr(self, "_serializer_related_fields", None)
        if cached is not None:
            return cached
        select_related, prefetch_related = [], []
        try:
            serializer_class = self.get_serializer_class()
            model = getattr(getattr(serializer_class, "Meta", None), "model", None)
            if model is None:
                raise ValueError(f"{serializer_class.__name__} has no Meta.model")
            for field_name, field in serializer_class().fields.items():
                source = field.source or field_name
                if source == "*":
                    continue
                if not getattr(field, "child_relation", None):
                    if not isinstance(field, serializers.RelatedField) or field.use_pk_only_optimization():
                        continue
                try:
                    model_field = model._meta.get_field(source)
                except FieldDoesNotExist:
                    continue
                if not model_field.is_relation:
                    continue
                related = select_related if model_field.many_to_one or model_field.one_to_one else prefetch_related
                if source not in related:
                    related.append(source)
        except Exception as e:
            logger.warning(f"auto infer prefetch related fields failed on {self.__class__.__name__}: {e}")
        self._serializer_related_fields = (select_related, prefetch_related)
        return self._serializer_related_fields

    def paginate_queryset(self, queryset):
        # 文件导出的时候，忽略 paginate_queryset
        if self.request.query_params.get("type") in ["csv", "xlsx"] and self.request.path_info.endswith("export-data"):
            return None
        return super().paginate_queryset(queryset)

    def get_serializer_class(self):
        action_serializer_name = f"{self.action}_serializer_class"
        action_serializer_class = getattr(self, action_serializer_name, None)
        if action_serializer_class:
            return action_serializer_class
        return super().get_serializer_class()
