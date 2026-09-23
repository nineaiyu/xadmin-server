#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""列表视图序列化器（F-4）。"""

from common.core.serializers import BaseModelSerializer
from system.models import SavedListView


class SavedListViewSerializer(BaseModelSerializer):
    class Meta:
        model = SavedListView
        fields = [
            "pk",
            "owner",
            "page_key",
            "name",
            "conditions",
            "ordering",
            "is_default",
            "is_shared",
            "remark",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "owner", "created_time", "updated_time"]
        table_fields = ["name", "page_key", "is_default", "is_shared", "updated_time"]
        extra_kwargs = {"owner": {"attrs": ["pk", "username", "nickname"], "read_only": True, "format": "{nickname}"}}
