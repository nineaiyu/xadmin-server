#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""受控 lookup 查询后端（?字段__op=value 高级筛选，自 filter.py 拆分，行为不变）。

字段面 = filterset 声明 field_name ∪ controlled_lookup_fields ∪ pk；lookup 白名单
九种；值按模型字段转换；字段可见性 fail-closed（非超管必须命中授权字段面）。"""

from functools import lru_cache
from typing import Any

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import (
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    FloatField,
    IntegerField,
    ManyToManyField,
    Q,
    TimeField,
)
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.fields import MultipleChoiceField
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.filters import BaseFilterBackend

from common.settings_contract import kernel_required_setting


class ControlledLookupFilterBackend(BaseFilterBackend):
    """受控 lookup 透传：在视图已声明过滤器的字段面上放开常用 lookup。

    开启方式（显式 opt-in，未开启的视图零变化）::

        class XxxViewSet(...):
            controlled_lookup = True
            extra_filter_class = [ControlledLookupFilterBackend]

    参数形态 ``field__lookup=value``（多个条件 AND 组合）：

    - 字段白名单 = ``filterset_class`` 已声明过滤器的 ``field_name`` 集合
      （可再用视图 ``controlled_lookup_fields`` 显式追加）——不扩大字段面，只放开 lookup；
    - lookup 限定 ``exact / icontains / startswith / in / gte / lte / isnull / ne``
      （``ne`` = 取反）；**禁跨关系嵌套**（``a__b__icontains`` 直接 400）；
    - **字段可见性 fail-closed**：非超管必须命中 ``request.fields`` 字段权限白名单，
      否则 400 —— 过滤不能成为无权字段的探测侧信道；
    - 值按模型字段 ``to_python`` 转换（失败 400，不落成 500）；``in`` 为逗号分隔多值；
      M2M 字段只允许 ``exact / in / ne``；
    - 条件数上限 ``max_conditions``（防参数滥用）。
    """

    allowed_lookups = ("exact", "icontains", "startswith", "in", "gte", "lte", "isnull", "ne")
    m2m_lookups = ("exact", "in", "ne")
    max_conditions = 20

    def filter_queryset(self, request: Any, queryset: Any, view: Any) -> Any:
        if not getattr(view, "controlled_lookup", False):
            return queryset
        keys = [key for key in request.query_params if "__" in key]
        if not keys:
            return queryset
        if len(keys) > self.max_conditions:
            raise RestValidationError(
                _("Too many filter conditions (at most %(count)s)") % {"count": self.max_conditions}
            )
        allowed_fields = self._allowed_fields(view)
        model = queryset.model
        model_label = model._meta.label_lower
        include = Q()
        exclude = Q()
        for key in keys:
            field_name, _separator, lookup = key.rpartition("__")
            if not field_name or "__" in field_name or lookup not in self.allowed_lookups:
                raise RestValidationError(_("Unsupported filter expression: %(key)s") % {"key": key})
            model_field = self._model_field(model, field_name)
            if model_field is None or field_name not in allowed_fields:
                raise RestValidationError(_("Unsupported filter field: %(key)s") % {"key": key})
            if isinstance(model_field, ManyToManyField) and lookup not in self.m2m_lookups:
                raise RestValidationError(_("Unsupported filter expression: %(key)s") % {"key": key})
            if not self._field_visible(request, model_label, field_name):
                raise RestValidationError(_("No permission to filter by field: %(field)s") % {"field": field_name})
            values = request.query_params.getlist(key)
            if lookup == "ne":
                exclude &= Q(**{field_name: self._coerce(model_field, values)})
                continue
            lookup_expr = field_name if lookup == "exact" else f"{field_name}__{lookup}"
            include &= Q(**{lookup_expr: self._coerce(model_field, values, lookup)})
        if exclude:
            queryset = queryset.exclude(exclude)
        return queryset.filter(include) if include else queryset

    # 按字段类型细化的可用表达式（下发前端高级筛选，避免给出后端必然拒绝的选项）
    range_lookups = ("exact", "in", "gte", "lte", "isnull", "ne")
    text_lookups = ("exact", "icontains", "startswith", "in", "isnull", "ne")
    bool_lookups = ("exact", "isnull", "ne")

    @classmethod
    def available_lookups(cls, model_field: Any) -> list[Any]:
        """字段类型对应的可用 lookup（与 filter_queryset 的判定同源）。"""
        if model_field is None:
            return []
        if isinstance(model_field, ManyToManyField):
            return list(cls.m2m_lookups)
        if isinstance(model_field, BooleanField):
            return list(cls.bool_lookups)
        if isinstance(model_field, (DateField, DateTimeField, TimeField, IntegerField, FloatField, DecimalField)):
            return list(cls.range_lookups)
        return list(cls.text_lookups)

    @classmethod
    def field_lookups(cls, view: Any, model_field: Any, field_name: str) -> list[Any]:
        """视图白名单命中的字段可用 lookup；未命中返回空列表（前端不下发该字段）。"""
        if model_field is None or field_name not in cls._allowed_fields(view):
            return []
        return cls.available_lookups(model_field)

    @classmethod
    def _allowed_fields(cls, view: Any) -> set[Any]:
        filterset_class = getattr(view, "filterset_class", None)
        # pk 恒可用（列表接口本就返回主键，不属于字段权限收敛面）
        fields = set(cls._filterset_fields(filterset_class)) if filterset_class is not None else {"pk"}
        fields |= set(getattr(view, "controlled_lookup_fields", ()) or ())
        return fields

    @staticmethod
    @lru_cache(maxsize=128)
    def _filterset_fields(filterset_class: Any) -> frozenset[Any]:
        """filterset 声明面的字段集合（进程内缓存）。

        ``get_filters()`` 会构造全部 Filter 对象；元数据下发对每一列都会调用一次
        （40 列表页约 40 次全量构造）。filterset 的声明面在类定义后不可变，按类缓存安全。
        """
        fields = {"pk"}
        for filter_obj in filterset_class.get_filters().values():
            field_name = getattr(filter_obj, "field_name", "") or ""
            if field_name and "__" not in field_name:
                fields.add(field_name)
        return frozenset(fields)

    @staticmethod
    def _model_field(model: Any, field_name: Any) -> Any:
        if field_name == "pk":
            return model._meta.pk
        try:
            return model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return None

    @staticmethod
    def _field_visible(request: Any, model_label: str, field_name: str) -> bool:
        """与序列化器字段裁剪同口径：超管全量；其余按 request.fields（fail-closed）。"""
        if not kernel_required_setting("PERMISSION_FIELD_ENABLED"):
            return True
        if field_name == "pk":
            return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_superuser", False):
            return True
        allowed = getattr(request, "fields", None)
        if not isinstance(allowed, dict):
            return False
        return field_name in (allowed.get(model_label) or ())

    def _coerce(self, model_field: Any, values: Any, lookup: str = "exact") -> Any:
        """查询参数值 → ORM 值。

        `values` 为同一参数名的重复值列表（``getlist``）：既支持 ``a,b`` 逗号分隔，
        也支持客户端数组序列化出的重复参数（``arrayFormat=repeat``）——此前只取
        第一个值，多值 ``in`` 会静默退化成单值。
        """
        first = str(values[0]) if values else ""
        if lookup == "isnull":
            return first.strip().lower() in ("1", "true", "yes", "on")
        if lookup == "in":
            items: list[str] = []
            for value in values:
                items.extend(item.strip() for item in str(value).split(",") if item.strip())
            if not items:
                raise RestValidationError(_("Invalid filter value for %(field)s") % {"field": model_field.name})
            return [self._to_python(model_field, item) for item in items]
        return self._to_python(model_field, first)

    @staticmethod
    def _to_python(model_field: Any, value: Any) -> Any:
        try:
            if isinstance(model_field, ManyToManyField):
                return model_field.target_field.to_python(value)
            if isinstance(model_field, BooleanField):
                # 布尔值容错：API 侧常用小写 true/false（Django 原生只认 True/False/"True"/"1"）
                normalized = str(value).strip().lower()
                if normalized in ("1", "true", "yes", "on"):
                    return True
                if normalized in ("0", "false", "no", "off"):
                    return False
                raise ValueError(f"invalid boolean: {value!r}")
            return model_field.to_python(value)
        except Exception as exc:
            raise RestValidationError(_("Invalid filter value for %(field)s") % {"field": model_field.name}) from exc


class PkMultipleChoiceField(MultipleChoiceField):
    def validate(self, value: Any) -> None:
        if self.required and not value:
            raise ValidationError(self.error_messages["required"], code="required")


class PkMultipleFilter(filters.MultipleChoiceFilter):
    """
    通过 input_type 来自定义前端展示类型
    """

    field_class = PkMultipleChoiceField

    def __init__(self, **kwargs: Any) -> None:
        self.input_type = kwargs.pop("input_type", None)
        super().__init__(**kwargs)
