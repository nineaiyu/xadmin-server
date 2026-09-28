#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : post
# author : ly_13
# date : 9/28/2026

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.utils import get_logger
from system.models import Post
from system.serializers.post import PostSerializer

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
        fields = ["pk", "name", "code", "dept", "dept_name", "rank", "is_active", "updated_time"]
        read_only_fields = [x.name for x in Post._meta.fields]


class SearchPostViewSet(OnlyListModelSet):
    """岗位搜索（通知选人等场景的远程搜索候选，仅启用岗位）"""

    queryset = Post.objects.all()
    serializer_class = SearchPostSerializer
    ordering_fields = ["rank", "name", "created_time"]
    filterset_class = SearchPostFilter

    def get_queryset(self):
        # 停用/软删除岗位不作为候选（与审批人解析口径一致）
        return super().get_queryset().filter(is_active=True, deleted_at__isnull=True).select_related("dept")
