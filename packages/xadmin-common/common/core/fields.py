#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : fields
# author : ly_13
# date : 8/6/2024
from typing import Any

import phonenumbers
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields_related import (  # noqa: F401 再导出：关联字段族导入面保持不变
    BasePrimaryKeyRelatedField as BasePrimaryKeyRelatedField,
)
from common.core.fields_related import (
    get_search_choices_max_count as get_search_choices_max_count,
)
from common.core.validators import ColorValidator, PhoneValidator


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
        """写入契约（与前端 RePlusPage 表单一致）：对象 ``{value, label}`` 或标量。

        - 对象形态取 ``value``（``{}`` / ``{"value": null}`` 归一为 None）；
        - 空选择（None / 空白串）按字段 ``allow_null`` 语义收口：不允许则明确报
          ``null`` 错，而不是把 ``{}`` 或空值写进库；
        - ``0`` / ``False`` 等合法枚举值不再被 falsy 短路绕过 choices 校验。
        """
        if isinstance(data, dict):
            data = data.get("value")
        if data is None or (isinstance(data, str) and not data.strip()):
            if not self.allow_null:
                self.fail("null")
            return None
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
        """写入契约：``[{value, label}, ...]`` 列表（清空为 ``[]``）。

        - 逐项取 ``value`` 并过滤未选择占位（前端默认值形态 ``[{}]`` /
          ``[{"value": null}]``），统一交给 MultipleChoiceField 做 choices 校验；
        - 空选择（None / 空白串）按 ``allow_null`` 语义收口；``[]`` 合法表示清空；
        - 返回列表形态，与既有 validated_data 消费面一致。
        """
        if data is None or data == "":
            if not self.allow_null:
                self.fail("null")
            return None
        if isinstance(data, (list, tuple)):
            values = []
            for item in data:
                value = item.get("value") if isinstance(item, dict) else item
                if value is None or value == "":
                    continue
                values.append(value)
            return list(super().to_internal_value(values))
        return list(super().to_internal_value(data))


class PhoneField(serializers.CharField):
    """手机号字段：``{code, phone}`` 对象或标量写入，E.164 存储、``{code, phone}`` 回显。

    写入契约：

    - 对象形态 ``{code, phone}`` 拼为 E.164（``+`` 可省略），``{code, phone:""}``
      与空串等价；
    - 空值按 ``allow_blank`` / ``allow_null`` 收口（优先空串，其次 ``None``，
      两者都不允许时抛标准 ``blank`` 错）——不再取决于消费方是否记得额外声明；
    - 默认捆绑 :class:`PhoneValidator`（``validate=False`` 可关闭），解析失败直接
      抛 ``ValidationError``，不再静默补 ``+86`` 入库；
    - 依赖历史「解析失败补地区码」行为的消费方须显式传 ``country="CN"``。
    """

    default_error_messages = {"invalid": _("The phone number format is incorrect")}

    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "phone"
        self.country = kwargs.pop("country", None)
        validate = kwargs.pop("validate", True)
        super().__init__(**kwargs)
        if validate and not any(isinstance(validator, PhoneValidator) for validator in self.validators):
            self.validators.append(PhoneValidator())

    def _empty_value(self) -> Any:
        if self.allow_blank:
            return ""
        if self.allow_null:
            return None
        self.fail("blank")

    def _normalize(self, data: Any) -> Any:
        region = self.country or "CN"
        try:
            phone = phonenumbers.parse(str(data), region)
            return f"+{phone.country_code}{phone.national_number}"
        except phonenumbers.NumberParseException:
            # 历史行为（解析失败盲目补地区码）仅在消费方显式声明 country 时保留；
            # 需同时关闭默认校验（validate=False）才能完整复现旧口径。
            if self.country:
                calling_code = phonenumbers.country_code_for_region(self.country)
                if calling_code:
                    return f"+{calling_code}{data}"
            raise serializers.ValidationError(self.error_messages["invalid"]) from None

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            code = data.get("code")
            phone = data.get("phone", "")
            data = f"{code}{phone}" if code and phone else phone
        if data is None:
            if self.allow_null:
                return None
            self.fail("null")
        if not isinstance(data, (str, int, float)):
            self.fail("invalid")
        if isinstance(data, str):
            data = data.strip()
        if not data:
            return self._empty_value()
        return super().to_internal_value(self._normalize(data))

    def to_representation(self, value: Any) -> Any:
        if not value:
            # 空号码（未填写）回显空结构：phonenumbers.parse(None, ...) 抛的是
            # TypeError（不在下方 except 捕获范围内），会让整个序列化 500
            return {"code": "+86", "phone": ""}
        try:
            phone = phonenumbers.parse(value, "CN")
            value = {"code": f"+{phone.country_code}", "phone": phone.national_number}
        except phonenumbers.NumberParseException:
            value = {"code": "+86", "phone": value}
        return value


class ColorField(serializers.CharField):
    """颜色值字段：默认捆绑格式校验（``#rgb``/``#rrggbbaa``、``rgb()``/``hsl()``、颜色名）。

    ``predefine`` 为前端色板预设（随元数据下发，缺省不影响既有 9 色兜底）。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "color"
        self.predefine = kwargs.pop("predefine", None)
        super().__init__(**kwargs)
        self.validators.append(ColorValidator())


class RichTextField(serializers.CharField):
    """富文本字段：入库前按 bleach 白名单净化（前端展示层再以 DOMPurify 兜底）。

    仅处理字符串内容；空值 / 非字符串由 ``CharField`` 原有语义接管。
    """

    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "rich_text"
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Any:
        value = super().to_internal_value(data)
        from common.utils.sanitize import sanitize_rich_text

        return sanitize_rich_text(value)


class PasswordField(serializers.CharField):
    """密码字段：仅标记 ``input_type=password``（前端遮蔽输入），加密与校验由消费方负责。"""

    def __init__(self, **kwargs: Any) -> None:
        self.input_type = "password"
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
