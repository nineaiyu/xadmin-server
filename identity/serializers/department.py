#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024
from typing import Any

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.filter import assert_within_data_scope, get_filter_queryset
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from identity.models import DeptInfo, UserInfo, UserRole
from system.services import DataPermission

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
            "managers",
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
            "managers",
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
            # 部门管理员只读展示：写口唯一 = assign-managers 端点（任命同时装配角色与数据权限）
            "managers": {
                "required": False,
                "read_only": True,
                "attrs": ["pk", "nickname", "username"],
                "format": "{nickname}({username})",
                "many": True,
            },
            "parent": {"required": False, "attrs": ["pk", "name", "parent_id"]},
        }

    # 关联计数声明：注解名与字段名一致，列表/详情/导出由 RelationCountMixin 预聚合
    relation_count_fields = {"user_count": Count("dept_query")}

    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))

    def validate(self, attrs: Any) -> Any:
        if self.instance is None:
            # 创建时未指定上级（未提交或显式置空）默认挂到操作者所在部门，保持既有新增体验
            if attrs.get("parent") is None:
                attrs["parent"] = self.request.user.dept
        elif "parent" in attrs and attrs["parent"] is not None:
            # 更新时未提交不动、显式提交 parent=null 允许提升为顶层部门（模型字段可空）；
            # 指定上级部门则做写侧载荷范围校验：非超管须在数据权限可见范围内
            assert_within_data_scope(
                DeptInfo.objects.filter(pk=attrs["parent"].pk),
                self.request.user,
                _("The superior department is outside your data scope"),
            )
        # 主管写入口与上级部门同口径：非超管指定的主管须在数据权限可见范围内
        leader = attrs.get("leader")
        if leader is not None:
            assert_within_data_scope(
                UserInfo.objects.filter(pk=leader.pk),
                self.request.user,
                _("The department leader is outside your data scope"),
            )
        return attrs

    def _assign_authorizations(self, instance: Any, roles: Any, rules: Any) -> None:
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
            # 与 roles 同口径过行级数据权限：部门编辑带上取值域外的规则时不挂载
            instance.rules.set(
                get_filter_queryset(
                    DataPermission.objects.filter(pk__in=[item.pk for item in rules if getattr(item, "pk", None)]),
                    self.request.user,
                ).all()
            )

    def create(self, validated_data: Any) -> Any:
        roles = validated_data.pop("roles", None)
        rules = validated_data.pop("rules", None)
        instance = super().create(validated_data)
        self._assign_authorizations(instance, roles, rules)
        return instance

    def update(self, instance: Any, validated_data: Any) -> Any:
        roles = validated_data.pop("roles", None)
        rules = validated_data.pop("rules", None)
        parent = validated_data.get("parent")
        if parent and str(parent.pk) in DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError(_("The superior department cannot be its own subordinate department"))
        instance = super().update(instance, validated_data)
        self._assign_authorizations(instance, roles, rules)
        return instance

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj: Any) -> Any:
        # 列表/详情/导出由 AnnotateUserCountMixin 预聚合，直接取聚合结果，避免每行一次 COUNT；
        # 未走该 mixin 的场景（直接序列化单个对象）回退为单对象聚合，结果保持一致
        count = getattr(obj, "user_count", None)
        return count if count is not None else obj.userinfo_set.count()


class DeptManagerAssignSerializer(serializers.Serializer):
    """部门管理员任命参数：``{add, remove}`` 增量变更（幂等，照岗位成员分配口径）。"""

    add = serializers.ListField(child=serializers.CharField(), required=False, label=_("Add managers"))
    remove = serializers.ListField(child=serializers.CharField(), required=False, label=_("Remove managers"))
