#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏规则序列化器。"""

from common.core.serializers import BaseModelSerializer
from system.models.mask import DataMaskRule


class DataMaskRuleSerializer(BaseModelSerializer):
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
