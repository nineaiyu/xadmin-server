#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件视图：与业务 app 同口径的 CRUD ViewSet（照抄 demo 的写法即可）。

要点：

- 继承 ``BaseModelSet``：一次拿到增删改查 + ``search-fields`` / ``search-columns``
  元数据端点（前端 RePlusPage 依赖它们装配搜索区与列表列）；
- 类注释必须写（菜单/操作日志展示该 docstring 作为资源名）；
- ``ControllerFilter`` 声明搜索字段与 ``Meta.fields``（``fields`` 的第 1 项即搜索区
  首字段，收起的搜索区只显示前几个字段，顺序即体验）；
- 删除/批量删除的引用保护由 Contracts 契约 ``guarded_models`` 驱动——本插件已在
  ``apps.py::ready()`` 把自有模型并入守卫清单，无需改视图。
"""

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.pagination import DynamicPageNumber
from xadmin_demo_plugin.models import PluginNote
from xadmin_demo_plugin.serializers import PluginNoteSerializer


class PluginNoteFilter(BaseFilterSet):
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")

    class Meta:
        model = PluginNote
        fields = ["title", "is_active", "created_time"]


class PluginNoteViewSet(BaseModelSet):
    """插件示例便签"""  # 注释即资源名（菜单、操作日志均取该 docstring）

    queryset = PluginNote.objects.all()
    serializer_class = PluginNoteSerializer
    filterset_class = PluginNoteFilter
    ordering_fields = ["created_time", "title"]  # 表头排序声明面（未声明不下发 sortable）
    pagination_class = DynamicPageNumber(1000)
