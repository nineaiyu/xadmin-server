#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.filter import get_filter_queryset
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import DataPermission, DeptInfo, UserRole

logger = get_logger(__name__)


class DeptSerializer(BaseModelSerializer):
    class Meta:
        model = DeptInfo
        fields = [
            "pk",
            "name",
            "code",
            "parent",
            "leader",
            "rank",
            "is_active",
            "roles",
            "user_count",
            "rules",
            "auto_bind",
            "description",
            "created_time",
        ]

        table_fields = [
            "name",
            "pk",
            "code",
            "user_count",
            "rank",
            "auto_bind",
            "is_active",
            "leader",
            "roles",
            "rules",
            "created_time",
        ]

        extra_kwargs = {
            "roles": {"required": False, "attrs": ["pk", "name", "code"], "format": "{name}", "many": True},
            "rules": {
                "required": False,
                "attrs": ["pk", "name", "get_mode_type_display"],
                "format": "{name}",
                "many": True,
            },
            "leader": {
                "required": False,
                "attrs": ["pk", "nickname", "username"],
                "format": "{nickname}({username})",
                # 用户表易超 SEARCH_CHOICES_MAX_COUNT 截断阈值，必须走远程搜索
                "input_type": "api-search-user",
            },
            "parent": {"required": False, "attrs": ["pk", "name", "parent_id"]},
        }

    # F-12 关联计数声明：注解名与字段名一致，列表/详情/导出由 RelationCountMixin 预聚合
    relation_count_fields = {"user_count": Count("dept_query")}

    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))

    def validate(self, attrs):
        # 上级部门必须存在，否则会出现数据权限问题
        parent = attrs.get("parent", self.instance.parent if self.instance else None)
        if not parent:
            attrs["parent"] = self.request.user.dept
        return attrs

    def _assign_authorizations(self, instance, roles, rules):
        """写入角色与数据权限：与专用授权接口同口径（角色取值域经行级数据权限过滤）。

        未传的项保持不变（保留既有授权），避免编辑部门信息时误清空。
        """
        if roles is not None:
            instance.roles.set(
                get_filter_queryset(
                    UserRole.objects.filter(pk__in=[item.pk for item in roles if getattr(item, "pk", None)]),
                    self.request.user,
                ).all()
            )
        if rules is not None:
            instance.rules.set(
                DataPermission.objects.filter(pk__in=[item.pk for item in rules if getattr(item, "pk", None)]).all()
            )

    def create(self, validated_data):
        roles = validated_data.pop("roles", None)
        rules = validated_data.pop("rules", None)
        instance = super().create(validated_data)
        self._assign_authorizations(instance, roles, rules)
        return instance

    def update(self, instance, validated_data):
        roles = validated_data.pop("roles", None)
        rules = validated_data.pop("rules", None)
        parent = validated_data.get("parent")
        if parent and str(parent.pk) in DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError(_("The superior department cannot be its own subordinate department"))
        instance = super().update(instance, validated_data)
        self._assign_authorizations(instance, roles, rules)
        return instance

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj):
        # 列表/详情/导出由 AnnotateUserCountMixin 预聚合，直接取聚合结果，避免每行一次 COUNT；
        # 未走该 mixin 的场景（直接序列化单个对象）回退为单对象聚合，结果保持一致
        count = getattr(obj, "user_count", None)
        return count if count is not None else obj.userinfo_set.count()
