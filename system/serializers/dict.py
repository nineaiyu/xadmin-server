#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典序列化器：类型/字典项两级树形展示与校验。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

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

    class Meta:
        model = DataDict
        fields = [
            "pk",
            "parent",
            "code",
            "label",
            "label_en",
            "value",
            "sort",
            "color",
            "is_active",
            "description",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "code",
            "label",
            "label_en",
            "value",
            "sort",
            "color",
            "is_active",
            "created_time",
        ]

    def get_unique_together_validators(self):
        """禁用 DRF 对 (parent, code) 约束生成的 UniqueTogetherValidator。

        DRF 3.16 对带 condition 的 UniqueConstraint 仍会生成校验器，导致类型层
        创建（parent 缺省）被误判「该字段不可为空」。唯一性由 validate() 显式
        查重，DB 侧保留部分唯一索引兜底并发。
        """
        return []

    def validate(self, attrs):
        """唯一性校验：类型层（parent 为空）code 全局唯一；字典项同类型下 code 唯一。

        DB 侧唯一约束带 condition（parent 非空），DRF 不做该约束校验，这里显式查重，
        避免撞约束 500。
        """
        parent = attrs.get("parent") or (self.instance.parent if self.instance else None)
        code = attrs.get("code") or (self.instance.code if self.instance else None)
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
