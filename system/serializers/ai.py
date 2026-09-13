#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库序列化器（ADR-033）：文档列表/预览 + 上传写入校验。

- 列表轻量（不含全文，to_representation 按 action 裁剪）；
- retrieve 附分块摘要（chunks）供前端预览「问答时会切成哪些块」；
- 上传写入走独立输入序列化器（name + content 文本），不落文件系统。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.ai import AiKnowledgeChunk, AiKnowledgeDocument
from system.serializers.task import DisplayRelatedField
from system.utils.ai import MAX_UPLOAD_CONTENT_LENGTH, MAX_UPLOAD_NAME_LENGTH, set_document_active

# 名称中的路径分隔符会破坏 upload/ 前缀隔离，统一拒绝
NAME_FORBIDDEN_CHARS = ("/", "\\")


class KnowledgeUploadSerializer(serializers.Serializer):
    """上传写入：name 唯一（同名覆盖更新）+ content 全文文本。"""

    name = serializers.CharField(max_length=MAX_UPLOAD_NAME_LENGTH)
    content = serializers.CharField(max_length=MAX_UPLOAD_CONTENT_LENGTH)

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError(_("Document name is required"))
        if any(char in name for char in NAME_FORBIDDEN_CHARS) or ".." in name:
            raise serializers.ValidationError(_("Document name cannot contain path characters"))
        return name

    def validate_content(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError(_("Document content cannot be empty"))
        return value


class AiKnowledgeDocumentSerializer(BaseModelSerializer):
    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Creator"), label_builder=lambda v: v.username
    )
    chunks = serializers.SerializerMethodField(label=_("Chunks"))

    class Meta:
        model = AiKnowledgeDocument
        fields = [
            "pk",
            "title",
            "source_type",
            "path",
            "content",
            "chunk_count",
            "is_active",
            "creator",
            "synced_at",
            "created_time",
            "updated_time",
            "chunks",
        ]
        # 仅 is_active 可写（启用/停用重建分块，见 update）；其余字段由上传/同步维护
        read_only_fields = [field for field in fields if field != "is_active"]
        table_fields = ["title", "source_type", "chunk_count", "is_active", "creator", "synced_at"]

    def get_chunks(self, obj) -> list:
        """分块摘要（仅详情返回）：问答检索时会命中的块清单。"""
        if getattr(self.context.get("view"), "action", None) != "retrieve":
            return []
        rows = (
            AiKnowledgeChunk.objects.filter(source_path=obj.path)
            .order_by("chunk_index")
            .values("chunk_index", "content")
        )
        return [
            {"index": row["chunk_index"], "size": len(row["content"]), "preview": row["content"][:120]} for row in rows
        ]

    def to_representation(self, instance):
        """列表轻量：全文与分块摘要只在详情（预览）返回，避免列表响应随文档量膨胀。"""
        data = super().to_representation(instance)
        if getattr(self.context.get("view"), "action", None) != "retrieve":
            data.pop("content", None)
            data.pop("chunks", None)
        return data

    def update(self, instance, validated_data):
        want_active = validated_data.pop("is_active", None)
        instance = super().update(instance, validated_data)
        if want_active is not None and bool(want_active) != instance.is_active:
            set_document_active(instance, want_active)
        return instance
