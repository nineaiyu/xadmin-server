#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通讯录（组织人员名录，只读）：按部门/岗位浏览在用用户。

- 只读：仅 list（含 search-columns/search-fields 元数据），无写动作；
- 数据权限随全局 BaseDataPermissionFilter 生效（调用者看不到的用户不在名录中）；
- 岗位维度筛选与审批人解析同口径：仅启用且未删除岗位的在岗用户
  （M2M join 不经过默认管理器，需显式过滤软删除——见 approval/conditions.py 同款注释）。
"""

from django.db.models import Q
from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from system.models import UserInfo
from system.serializers.directory import DirectorySerializer


class DirectoryFilter(BaseFilterSet):
    keyword = filters.CharFilter(method="filter_keyword")

    class Meta:
        model = UserInfo
        fields = ["dept", "posts", "gender"]

    def filter_keyword(self, queryset, name, value):
        value = str(value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(username__icontains=value)
            | Q(nickname__icontains=value)
            | Q(email__icontains=value)
            | Q(phone__icontains=value)
        )


class DirectoryViewSet(OnlyListModelSet):
    """通讯录"""

    queryset = UserInfo.objects.filter(is_active=True)
    serializer_class = DirectorySerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ["username", "date_joined", "last_login"]
    filterset_class = DirectoryFilter
