#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成方案序列化器。"""

from common.core.serializers import BaseModelSerializer
from system.models import CodegenPlan


class CodegenPlanSerializer(BaseModelSerializer):
    class Meta:
        model = CodegenPlan
        fields = [
            "pk",
            "name",
            "payload",
            "description",
            "is_shared",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "creator", "created_time", "updated_time"]
        extra_kwargs = {
            "creator": {"attrs": ["pk", "username", "nickname"], "read_only": True, "format": "{nickname}"},
        }
