#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏规则序列化器。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from audit.models.mask import DataMaskRule
from audit.utils.mask import custom_pattern_error
from common.core.serializers import BaseModelSerializer
from system.services.modelfield import model_field_known


class DataMaskRuleSerializer(BaseModelSerializer):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """自定义正则试编译 + 模型/字段存在性校验。

        手填错误的 model.field 会得到一条永远不命中的规则；新建或改动
        model/field 时按字段注册表（ROLE 字段树）硬校验，存量行仅改其他字段
        时不做存在性回查，避免注册表尚未同步时阻断保存。
        """
        if self.instance is not None:
            mask_type = attrs.get("mask_type", self.instance.mask_type)
            pattern = attrs.get("pattern", self.instance.pattern)
            model = attrs.get("model", self.instance.model)
            field = attrs.get("field", self.instance.field)
        else:
            mask_type = attrs.get("mask_type") or DataMaskRule.MaskType.CUSTOM
            pattern = attrs.get("pattern")
            model = attrs.get("model")
            field = attrs.get("field")
        if mask_type == DataMaskRule.MaskType.CUSTOM and pattern:
            error = custom_pattern_error(pattern)
            if error is not None:
                raise ValidationError(
                    {
                        "pattern": _("Invalid custom regex: %(pattern)s (%(reason)s)")
                        % {"pattern": pattern, "reason": error}
                    }
                ) from error
        if model and field and (self.instance is None or "model" in attrs or "field" in attrs):
            if not model_field_known(model, field):
                raise ValidationError({"field": _("Unknown model field: {}.{}").format(model, field)})
        return attrs

    class Meta:
        model = DataMaskRule
        fields = [
            "pk",
            "model",
            "field",
            "mask_type",
            "keep_head",
            "keep_tail",
            "mask_char",
            "pattern",
            "roles",
            "is_active",
            "sort",
            "description",
            "creator",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "model",
            "field",
            "mask_type",
            "keep_head",
            "keep_tail",
            "mask_char",
            "pattern",
            "roles",
            "is_active",
            "sort",
            "creator",
            "updated_time",
        ]
        read_only_fields = ["pk", "creator"]
        extra_kwargs = {"roles": {"attrs": ["pk", "name"], "many": True, "input_type": "api-search-role"}}
