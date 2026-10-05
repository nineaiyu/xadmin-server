#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : post
# author : ly_13
# date : 9/28/2026

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet, RelationCountMixin
from common.utils import get_logger
from identity.models import Post
from identity.serializers.post import PostSerializer

logger = get_logger(__name__)


class SearchPostFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    code = filters.CharFilter(field_name="code", lookup_expr="icontains")

    class Meta:
        model = Post
        fields = ["name", "code", "is_active", "dept"]


class SearchPostSerializer(PostSerializer):
    class Meta:
        model = Post
        fields = ["pk", "name", "code", "dept", "dept_name", "rank", "is_active", "user_count", "updated_time"]
        read_only_fields = [x.name for x in Post._meta.fields]


class SearchPostViewSet(RelationCountMixin, OnlyListModelSet):
    """岗位搜索（通知选人等场景的远程搜索候选，仅启用岗位）

    候选清单带成员数（``user_count``，与岗位管理页同口径），供人员名录类页面
    展示岗位规模；成员数注解由 RelationCountMixin 按序列化器声明预聚合。
    """

    queryset = Post.objects.all()
    serializer_class = SearchPostSerializer
    ordering_fields = ["rank", "name", "created_time"]
    filterset_class = SearchPostFilter

    def get_queryset(self):
        # 停用/软删除岗位不作为候选（与审批人解析口径一致）
        return super().get_queryset().filter(is_active=True, deleted_at__isnull=True).select_related("dept")
