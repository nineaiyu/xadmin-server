#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""列表关联计数声明式（F-12）。

序列化器声明计数表达式（**注解名与字段名一致**，序列化器侧取值并保留回退）：

.. code-block:: python

    class RoleSerializer(BaseModelSerializer):
        relation_count_fields = {"user_count": Count("userinfo")}

视图混入本 mixin 后，列表 / 详情 / 导出（``auto_prefetch_actions``）自动把
``Count(...)`` 注解并入主查询，消除逐行 COUNT（N+1）；写操作不注解。

两个必须注意的实现细节（与存量 ``AnnotateUserCountMixin`` 同口径）：

1. ``annotate`` 产生 GROUP BY 后 Django 不再套用 ``Meta.ordering``，须显式补回，
   否则分页顺序不稳定（前端传 ordering 时 OrderingFilter 会在其后覆盖）；
2. 反向查询名以模型声明为准（如 ``UserInfo.dept`` 显式声明
   ``related_query_name="dept_query"``；``UserInfo.roles`` 未声明时用默认反向名
   ``userinfo``）——写错会在查询期抛 FieldError。

与 F-2 影响面**同源**：计数表达式与 ``system/utils/impact.py`` 的引用计算器指向同一关系
（角色→用户、数据集→报表），「列表计数」与「删除前影响面」回答同一件事。

未声明 ``relation_count_fields`` 的视图行为零变化。存量两处「注解名与字段名不一致」的
手写实现（字典 ``children_count``、审批流程 ``node_count``）已迁移到本声明式口径。
"""

from common.utils import get_logger

logger = get_logger(__name__)


class RelationCountMixin:
    """按序列化器声明为 queryset 预聚合关联计数。"""

    def get_queryset(self):
        queryset = super().get_queryset()
        counts = self._relation_count_fields()
        if not counts:
            return queryset
        if getattr(self, "action", None) not in getattr(self, "auto_prefetch_actions", ()):
            return queryset
        ordering = queryset.query.order_by or queryset.model._meta.ordering
        queryset = queryset.annotate(**counts)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset

    def _relation_count_fields(self) -> dict:
        serializer_class = getattr(self, "serializer_class", None)
        if hasattr(self, "get_serializer_class"):
            try:
                serializer_class = self.get_serializer_class()
            except Exception:  # pragma: no cover - schema 生成 / action 未就绪等边界
                logger.debug("relation count 解析序列化器失败，回退 serializer_class", exc_info=True)
        return dict(getattr(serializer_class, "relation_count_fields", None) or {})
