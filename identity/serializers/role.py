#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : role
# author : ly_13
# date : 8/10/2024
from typing import Any

from django.db import transaction
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from common.core.validation import ActiveUniqueValidationMixin
from common.utils import get_logger
from identity.models import UserRole
from system.services import FieldPermission

logger = get_logger(__name__)


class FieldPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = FieldPermission
        fields = ["pk", "role", "menu", "field"]
        read_only_fields = ["pk"]


class RoleSerializer(ActiveUniqueValidationMixin, BaseModelSerializer):
    class Meta:
        model = UserRole
        fields = [
            "pk",
            "name",
            "code",
            "is_active",
            "builtin",
            "user_count",
            "description",
            "menu",
            "updated_time",
            "field",
            "fields",
        ]
        table_fields = ["pk", "name", "code", "is_active", "builtin", "user_count", "description", "updated_time"]
        read_only_fields = ["pk", "builtin"]
        extra_kwargs = {"menu": {"attrs": ["pk", "name"], "many": True, "input_type": "input"}}

    # 上面写的 extra_kwargs['menu'] 和下面下结果一样，但是上面写法少写了 label 和 queryset
    # menu = BasePrimaryKeyRelatedField(queryset=Menu.objects, many=True, label=_("Menu"), attrs=['pk', 'name'],
    #                                   input_type="input")

    # field和fields 设置两个相同的label，可以进行文件导入导出
    field = serializers.SerializerMethodField(read_only=True, label=_("Fields"))
    fields = serializers.DictField(write_only=True, label=_("Fields"))

    # 关联计数声明：列表/详情/导出由 RelationCountMixin 预聚合（与影响面同源，
    # 角色→用户为「删除/停用影响谁」的同一口径）；单对象序列化回退为单次 COUNT
    relation_count_fields = {"user_count": Count("userinfo")}
    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj: Any) -> Any:
        count = getattr(obj, "user_count", None)
        return count if count is not None else obj.userinfo_set.count()

    def validate_name(self, value: Any) -> Any:
        # 唯一性为「未删除数据」条件约束（见 Meta.constraints），走共享 mixin 显式校验
        return self._validate_active_unique("name", value)

    def validate_code(self, value: Any) -> Any:
        return self._validate_active_unique("code", value)

    def validate(self, attrs: Any) -> Any:
        # 内置角色不可改 code（code 被代码与治理配置引用）；create 占用内置 code
        # 已由 validate_code 拦截，这里拦 update 改名场景
        if self.instance is not None and self.instance.builtin:
            new_code = attrs.get("code", self.instance.code)
            if new_code != self.instance.code:
                raise serializers.ValidationError({"code": _("Builtin role code cannot be changed")})
        return attrs

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_field(self, obj: Any) -> Any:
        # 前端授权树回显契约（treeKeys.ts）：{menuPk: [fieldPk]}，pk 一律为纯字符串。
        # 不能经 FieldPermissionSerializer 取值：BasePrimaryKeyRelatedField 默认输出
        # {'pk':..., 'label':...} 结构，会把字典键变成 str(dict)、值变成 dict 列表，
        # 前端合成键匹配不到树节点，勾选回显全丢（表现为字段权限设置后不渲染）。
        data = {}
        for fp in FieldPermission.objects.filter(role=obj).prefetch_related("field"):
            data[str(fp.menu_id)] = [str(item.pk) for item in fp.field.all()]
        return data

    def save_fields(self, fields: Any, instance: Any) -> None:
        for k, v in fields.items():
            serializer = FieldPermissionSerializer(
                data={"role": instance.pk, "menu": k, "field": v}, ignore_field_permission=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

    def update(self, instance: Any, validated_data: Any) -> Any:
        fields = validated_data.pop("fields", None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if fields:
                FieldPermission.objects.filter(role=instance).delete()
                self.save_fields(fields, instance)
        return instance

    def create(self, validated_data: Any) -> Any:
        fields = validated_data.pop("fields")
        with transaction.atomic():
            instance = super().create(validated_data)
            self.save_fields(fields, instance)
        return instance


class ListRoleSerializer(RoleSerializer):
    class Meta:
        model = UserRole
        fields = [
            "pk",
            "name",
            "is_active",
            "code",
            "user_count",
            "menu",
            "builtin",
            "description",
            "updated_time",
            "deleted_at",
            "field",
            "fields",
        ]
        # 主列表列白名单：deleted_at（回收站口径）/ field / fields 不上主表格
        table_fields = [
            "pk",
            "name",
            "is_active",
            "code",
            "user_count",
            "menu",
            "builtin",
            "description",
            "updated_time",
        ]
        read_only_fields = [x.name for x in UserRole._meta.fields]

    # 列表行的 field 刻意只是空列表占位（不逐行回显字段权限字典：整页逐行查
    # FieldPermission 的查询与体积成本都不可接受），编辑回显以 retrieve 的
    # get_field 逐对象输出为准
    field = serializers.ListField(default=[], read_only=True)
    menu = serializers.SerializerMethodField(read_only=True)

    @extend_schema_field(serializers.ListField)
    def get_menu(self, instance: Any) -> Any:
        return []
