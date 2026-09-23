#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心（P-1）序列化器：标签 CRUD + 可打标对象的只读 tags 字段。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.tag import TAGGABLE_MODELS, Tag
from system.serializers.task import DisplayRelatedField

#: 标签颜色白名单（Element Plus 语义色 + 自定义十六进制，前端渲染 tag 用）
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class TagSerializer(BaseModelSerializer):
    """标签定义：名称唯一 + 颜色 + 使用计数（列表标注，删除保护的数据面）。"""

    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Creator"), label_builder=lambda v: v.username
    )
    usage_count = serializers.SerializerMethodField(label=_("Usage count"))

    class Meta:
        model = Tag
        fields = [
            "pk",
            "name",
            "color",
            "builtin",
            "remark",
            "creator",
            "created_time",
            "updated_time",
            "usage_count",
        ]
        table_fields = ["name", "color", "usage_count", "builtin", "remark", "updated_time"]

    def get_usage_count(self, obj) -> int:
        annotated = getattr(obj, "usage_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.tagged_items.count()

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError(_("Tag name is required"))
        return name

    def validate_color(self, value):
        color = (value or "").strip()
        if color and not color.startswith("#"):
            raise serializers.ValidationError(_("Color must be a hex value like #409EFF"))
        return color


class TagAssignSerializer(serializers.Serializer):
    """单对象打标（全量替换语义）：``{resource, pk, tags: [tag_pk]}``。"""

    resource = serializers.CharField(max_length=128)
    pk = serializers.CharField(max_length=64)
    tags = serializers.ListField(child=serializers.CharField(max_length=64), required=False, default=list)

    def validate_resource(self, value):
        key = (value or "").strip().lower()
        if key not in TAGGABLE_MODELS:
            raise serializers.ValidationError(_("The object type cannot be tagged"))
        return key


class TagBatchAssignSerializer(TagAssignSerializer):
    """批量打标：``{resource, pks: [...], tags: [...], mode: replace|add|remove}``。"""

    pks = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=False)
    pk = serializers.CharField(max_length=64, required=False, allow_blank=True)
    mode = serializers.ChoiceField(choices=("replace", "add", "remove"), required=False, default="add")


class TaggedObjectSerializerMixin:
    """可打标对象的序列化器混入：只读 ``tags`` 字段（列表/详情通用）。

    依赖视图声明 ``prefetch_related_fields = ("tagged_items__tag",)`` 消除 N+1。
    """

    def get_tags(self, obj) -> list:
        from system.utils.tags import tags_for_instance

        return tags_for_instance(obj)
