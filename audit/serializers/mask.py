#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏规则序列化器。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from audit.models.mask import DataMaskRule
from audit.utils.mask import custom_pattern_error
from common.core.serializers import BaseModelSerializer


class DataMaskRuleSerializer(BaseModelSerializer):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """自定义正则保存期试编译：非法正则入口即拒，避免流入运行时静默回退未脱敏原值。

        PATCH 未携带的字段取实例现值（含 mask_type 改为 custom 时校验存量 pattern）；
        创建时 mask_type 缺省按模型默认 custom 参与判定。
        """
        if self.instance is not None:
            mask_type = attrs.get("mask_type", self.instance.mask_type)
            pattern = attrs.get("pattern", self.instance.pattern)
        else:
            mask_type = attrs.get("mask_type") or DataMaskRule.MaskType.CUSTOM
            pattern = attrs.get("pattern")
        if mask_type == DataMaskRule.MaskType.CUSTOM and pattern:
            error = custom_pattern_error(pattern)
            if error is not None:
                raise ValidationError(
                    {
                        "pattern": _("Invalid custom regex: %(pattern)s (%(reason)s)")
                        % {"pattern": pattern, "reason": error}
                    }
                ) from error
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
