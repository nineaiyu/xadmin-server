#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典序列化器：类型/字典项两级树形展示与校验。

- color 用 ColorField（input_type=color），前端自动渲染为颜色选择器；
- parent_code 只读输出、写入时按类型编码解析父级：导出的表格用编码而非 UUID
  主键定位所属类型，跨环境导入仍可复用（parent 主键在新环境无意义）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import ColorField
from common.core.serializers import BaseModelSerializer
from system.models.dict import DataDict
from system.serializers.task import DisplayRelatedField


class DataDictSerializer(BaseModelSerializer):
    # parent 可写（表单选择所属字典类型），输出 {pk,label} 供前端直接可读。
    # queryset 限定类型层（parent 为空）：字典项不能作为父级，杜绝三级/自引用结构——
    # get_dict_items 只按两级查询，深层数据会变成永远取不到的孤儿
    parent = DisplayRelatedField(
        queryset=DataDict.objects.filter(parent__isnull=True),
        label_builder=lambda value: value.label,
        required=False,
        allow_null=True,
        label=_("Parent dict"),
    )
    color = ColorField(required=False, allow_blank=True, allow_null=True, label=_("Tag color"))
    # 所属类型编码：导入时用编码定位父级，导出时给出可读的类型标识
    parent_code = serializers.CharField(source="parent.code", read_only=True, label=_("Parent code"))
    children_count = serializers.SerializerMethodField(label=_("Children count"))

    class Meta:
        model = DataDict
        fields = [
            "pk",
            "parent",
            "parent_code",
            "code",
            "label",
            "label_en",
            "value",
            "sort",
            "color",
            "is_active",
            "is_locked",
            "children_count",
            "description",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "parent_code",
            "code",
            "label",
            "label_en",
            "value",
            "sort",
            "color",
            "is_active",
            "is_locked",
            "children_count",
            "created_time",
        ]

    def get_unique_together_validators(self):
        """禁用 DRF 对 (parent, code) 约束生成的 UniqueTogetherValidator。

        DRF 3.16 对带 condition 的 UniqueConstraint 仍会生成校验器，导致类型层
        创建（parent 缺省）被误判「该字段不可为空」。唯一性由 validate() 显式
        查重，DB 侧保留部分唯一索引兜底并发。
        """
        return []

    def get_children_count(self, obj) -> int:
        """字典项数量（仅类型行有意义）：视图已 annotate 时直接取值，避免 N+1。"""
        count = getattr(obj, "children_count", None)
        if count is None:
            return 0 if obj.parent_id else obj.children.count()
        return count

    def to_internal_value(self, data):
        """写入前把 parent_code（字典类型编码）翻译成 parent 主键。

        parent_code 是只读的 SerializerMethodField 式字段（source=parent.code），
        不能直接接收写入，这里在校验前消费掉：Excel 导入的表格里带的是类型编码，
        parent 主键只在同一环境内有效。
        """
        parent_code = data.get("parent_code") if hasattr(data, "get") else None
        result = super().to_internal_value(data)
        if parent_code and not result.get("parent"):
            parent = DataDict.objects.filter(parent__isnull=True, code=parent_code).first()
            if parent is None:
                raise serializers.ValidationError({"parent_code": _("Parent dict does not exist")})
            result["parent"] = parent
        return result

    def validate(self, attrs):
        """唯一性校验：类型层（parent 为空）code 全局唯一；字典项同类型下 code 唯一。

        DB 侧唯一约束带 condition（parent 非空），DRF 不做该约束校验，这里显式查重，
        避免撞约束 500。内置字典（is_locked）额外禁止改 code 与父级。
        """
        parent = attrs.get("parent") or (self.instance.parent if self.instance else None)
        code = attrs.get("code") or (self.instance.code if self.instance else None)
        if self.instance and self.instance.is_locked:
            # 锁定字典被代码按 code 引用（DictChoiceField），改 code 等于让引用断链
            if code and code != self.instance.code:
                raise serializers.ValidationError({"code": _("Locked dict cannot be modified")})
            if parent is not None and parent.pk != self.instance.parent_id:
                raise serializers.ValidationError({"parent": _("Locked dict cannot be modified")})
            return attrs
        if not code:
            return attrs
        if parent and self.instance and parent.pk == self.instance.pk:
            # 父级 queryset 已限定类型层，但类型行自身也在其中，需显式拒绝自引用
            raise serializers.ValidationError({"parent": _("Parent dict cannot be itself")})
        exists = (
            DataDict.objects.filter(parent=parent, code=code)
            .exclude(pk=self.instance.pk if self.instance else None)
            .exists()
        )
        if exists:
            raise serializers.ValidationError({"code": _("Dict code already exists")})
        return attrs
