#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : fields
# author : ly_13
# date : 8/6/2024
from typing import Any

import phonenumbers
from rest_framework import serializers

from common.core.fields_related import (  # noqa: F401 再导出：关联字段族导入面保持不变
    BasePrimaryKeyRelatedField as BasePrimaryKeyRelatedField,
)
from common.core.fields_related import (
    get_search_choices_max_count as get_search_choices_max_count,
)


class LabeledChoiceField(serializers.ChoiceField):
    def __init__(self, **kwargs: Any) -> None:
        self.attrs = kwargs.pop("attrs", None) or ("value", "label")
        super().__init__(**kwargs)

    def to_representation(self, key: str) -> Any:
        if key is None:
            return key
        # label 可能是 gettext_lazy 代理（模型枚举 choices）：必须物化为 str，
        # 否则该 payload 走 channels-redis msgpack 序列化（WS 推送）会抛
        # can not serialize '__proxy__'（JSON 路径会隐式 force_str 掩盖此问题）
        label = str(self.choices.get(key, key))
        return {"value": key, "label": label}

    def to_internal_value(self, data: Any) -> Any:
        if not data:
            return data
        if isinstance(data, dict):
            data = data.get("value")
        if isinstance(data, str) and "(" in data and data.endswith(")"):
            data = data.strip(")").split("(")[-1]
        return super().to_internal_value(data)

    def get_schema(self) -> Any:
        """
        为 drf-spectacular 提供 OpenAPI schema
        """
        if getattr(self, "many", False):
            return {
                "type": "array",
                "items": {"type": "object", "properties": {"value": {"type": "string"}, "label": {"type": "string"}}},
                "description": getattr(self, "help_text", ""),
                "title": getattr(self, "label", ""),
            }
        else:
            return {
                "type": "object",
                "properties": {"value": {"type": "string"}, "label": {"type": "string"}},
                "description": getattr(self, "help_text", ""),
                "title": getattr(self, "label", ""),
            }


class LabeledMultipleChoiceField(serializers.MultipleChoiceField):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.choice_mapper = {key: value for key, value in self.choices.items()}

    def to_representation(self, keys: Any) -> Any:
        if keys is None:
            return keys
        return [{"value": key, "label": self.choice_mapper.get(key)} for key in keys]

    def to_internal_value(self, data: Any) -> Any:
        if not data:
            return data

        if isinstance(data[0], dict):
            return [item.get("value") for item in data]
        else:
            return data


class PhoneField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "phone"
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            code = data.get("code")
            phone = data.get("phone", "")
            if code and phone:
                code = code.replace("+", "")
                data = f"+{code}{phone}"
            else:
                data = phone
        if data:
            try:
                phone = phonenumbers.parse(data, "CN")
                data = f"+{phone.country_code}{phone.national_number}"
            except phonenumbers.NumberParseException:
                data = f"+86{data}"

        return super().to_internal_value(data)

    def to_representation(self, value: Any) -> Any:
        try:
            phone = phonenumbers.parse(value, "CN")
            value = {"code": f"+{phone.country_code}", "phone": phone.national_number}
        except phonenumbers.NumberParseException:
            value = {"code": "+86", "phone": value}
        return value


class ColorField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "color"
        super().__init__(**kwargs)


class StepFloatField(serializers.FloatField):
    """带步进的数值字段：``step`` 随 search-columns 元数据下发，前端 input-number 消费。

    min_value/max_value 是 DRF 标准属性（SimpleMetadata.get_field_info 原生下发）；
    step 是扩展属性，由 metadata_columns 显式透出（字段显式声明才下发，缺省不影响
    其他数值字段的既有渲染）。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.step = kwargs.pop("step", None)
        super().__init__(**kwargs)


# 数据字典驱动字段拆分至 fields_dict.py（文件行数门禁）；此处保留兼容再导出，
# 既有 `from common.core.fields import DictChoiceField / register_dict_items_resolver`
# 的消费点无需改动，新代码建议直接 import common.core.fields_dict
from common.core.fields_dict import (  # noqa: E402,F401
    DictChoiceField as DictChoiceField,
)
from common.core.fields_dict import (
    register_dict_items_resolver as register_dict_items_resolver,
)
