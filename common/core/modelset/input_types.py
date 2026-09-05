#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段输入类型辅助：根据 serializer 字段推断前端渲染用的 input_type。

供元数据 Action（search-columns / search-fields）使用。拆分自 modelset.py（T2.1）。
"""

from common.core.serializers import BasePrimaryKeyRelatedField


def get_upload_input_type_suffix(value, default):
    if hasattr(value, "child_relation"):
        value = value.child_relation
    try:
        if (
            value.queryset.model._meta.label == "system.UploadFile"
            and isinstance(value, BasePrimaryKeyRelatedField)
            and default in ["object_related_field", "m2m_related_field"]
        ):
            return "_file"
    except Exception:
        pass
    return ""


def get_format_intput_type(value, default=""):
    input_type_prefix = ""
    input_type = default
    input_type_suffix = get_upload_input_type_suffix(value, default)

    if hasattr(value, "input_type") and value.input_type is not None:
        input_type = value.input_type
    if hasattr(value, "input_type_prefix") and value.input_type_prefix is not None:
        input_type_prefix = f"{value.input_type_prefix}_" if value.input_type_prefix else ""
    if hasattr(value, "input_type_suffix") and value.input_type_suffix is not None:
        input_type_suffix = f"_{value.input_type_suffix}" if value.input_type_suffix else ""
    input_type_str = input_type_prefix + input_type + input_type_suffix
    if input_type_str:
        return input_type_str
    return default
