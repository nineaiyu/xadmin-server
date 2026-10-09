#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""岗位序列化器：表格列 + 关联计数（成员数）+ 未删除唯一校验。"""

from typing import Any

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from common.core.validation import ActiveUniqueValidationMixin
from identity.models import Post


class PostMemberSerializer(serializers.Serializer):
    """岗位成员变更载荷：增量 add / remove（幂等），至少给出一侧。"""

    add = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    remove = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)

    def validate(self, attrs: Any) -> Any:
        if not attrs.get("add") and not attrs.get("remove"):
            raise serializers.ValidationError(_("Provide members to add or remove"))
        return attrs


class PostSerializer(ActiveUniqueValidationMixin, BaseModelSerializer):
    class Meta:
        model = Post
        fields = [
            "pk",
            "name",
            "code",
            "dept",
            "dept_name",
            "rank",
            "is_active",
            "user_count",
            "description",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "name",
            "code",
            "dept_name",
            "user_count",
            "is_active",
            "description",
            "updated_time",
        ]
        read_only_fields = ["pk"]

    dept_name = serializers.SerializerMethodField(read_only=True, label=_("Dept"))

    @extend_schema_field(OpenApiTypes.STR)
    def get_dept_name(self, obj: Any) -> Any:
        return obj.dept.name if obj.dept_id else ""

    # 关联计数声明：列表/详情/导出由 RelationCountMixin 预聚合（成员数），
    # 单对象序列化回退为单次 COUNT
    relation_count_fields = {"user_count": Count("post_query")}
    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj: Any) -> Any:
        count = getattr(obj, "user_count", None)
        return count if count is not None else obj.users.count()

    def validate_name(self, value: Any) -> Any:
        # 唯一性为「未删除数据」条件约束（见 Meta.constraints），走共享 mixin 显式校验
        return self._validate_active_unique("name", value)

    def validate_code(self, value: Any) -> Any:
        return self._validate_active_unique("code", value)
