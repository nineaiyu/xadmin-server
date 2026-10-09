#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件序列化器：与业务 app 同口径（字段权限 / 搜索列元数据 / 导入导出）。

``table_fields`` 决定前端列表列顺序与搜索区字段（不含该声明时回落 ``fields``），
``extra_kwargs`` 是关联字段（creator 等）的渲染契约——写法与业务 app 完全一致，
照抄 ``demo/serializers/book.py`` 即可。
"""

from common.core.serializers import BaseModelSerializer
from xadmin_demo_plugin.models import PluginNote


class PluginNoteSerializer(BaseModelSerializer):
    class Meta:
        model = PluginNote
        fields = ["pk", "title", "content", "is_active", "creator", "created_time", "updated_time"]
        table_fields = ["pk", "title", "is_active", "creator", "created_time"]
        extra_kwargs = {
            "pk": {"read_only": True},
            "creator": {
                "attrs": ["pk", "username"],
                "read_only": True,
                "format": "{username}({pk})",
                "input_type": "api-search-user",
            },
        }
