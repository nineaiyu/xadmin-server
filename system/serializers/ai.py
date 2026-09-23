#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库序列化器：文档列表/预览 + 上传写入校验。

- 列表轻量（不含全文，to_representation 按 action 裁剪）；
- retrieve 附分块摘要（chunks）供前端预览「问答时会切成哪些块」；
- 上传写入走独立输入序列化器（name + content 文本），不落文件系统。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.base.utils import signer
from common.core.serializers import BaseModelSerializer
from system.models.ai import AiKnowledgeChunk, AiKnowledgeDocument, AiProfile
from system.serializers.task import DisplayRelatedField
from system.utils.ai import MAX_UPLOAD_CONTENT_LENGTH, MAX_UPLOAD_NAME_LENGTH, set_document_active

# 名称中的路径分隔符会破坏 upload/ 前缀隔离，统一拒绝
NAME_FORBIDDEN_CHARS = ("/", "\\")


def _encrypt_api_key(value: str) -> str:
    value = (value or "").strip()
    return signer.encrypt(value.encode("utf-8")).decode("utf-8") if value else ""


class AiProfileSerializer(BaseModelSerializer):
    """AI 配置档案：api_key 明文进 → 加密存；回显只给 api_key_set 布尔，永不回传密钥。

    capabilities 为能力探测结果，可由管理端 PATCH 手工修正（探测结果允许覆盖）。
    """

    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Creator"), label_builder=lambda v: v.username
    )
    api_key = serializers.CharField(
        required=False, allow_blank=True, write_only=True, max_length=512, label=_("API Key")
    )
    api_key_set = serializers.SerializerMethodField(label=_("API Key set"))
    # 显式声明绕开 DRF 3.16 对单字段 UniqueConstraint 自动生成的 UniqueValidator：
    # 激活互斥由 service 层 set_active_profile「自动顶掉旧档案」保证，不是报错语义
    is_active = serializers.BooleanField(required=False, default=False, label=_("Is active"))
    purpose = serializers.ChoiceField(choices=AiProfile.Purpose.choices, required=False, label=_("Purpose"))
    capabilities = serializers.JSONField(required=False, label=_("Capabilities"))

    class Meta:
        model = AiProfile
        fields = [
            "pk",
            "name",
            "base_url",
            "api_key",
            "api_key_set",
            "model",
            "temperature",
            "max_tokens",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "stop",
            "seed",
            "timeout",
            "max_retries",
            "context_limit",
            "persona",
            "purpose",
            "capabilities",
            "probed_at",
            "is_active",
            "remark",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["api_key_set", "probed_at"]
        table_fields = [
            "name",
            "purpose",
            "model",
            "temperature",
            "max_tokens",
            "is_active",
            "remark",
            "updated_time",
        ]

    def get_api_key_set(self, obj) -> bool:
        return bool(obj.api_key)

    def validate_capabilities(self, value):
        """能力画像手工修正：结构必须为「能力名 → 结果 dict」+ 可选 model/probed_at 元信息。

        结构错误直接拒绝，避免前端误写导致链路判据（tool_calls 准入）读到脏数据。
        """
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Capabilities must be an object"))
        for key, item in value.items():
            if key in ("model", "probed_at"):
                continue
            if not isinstance(item, dict):
                raise serializers.ValidationError(_("Capabilities must map capability names to objects"))
            if "ok" in item and not isinstance(item["ok"], bool):
                raise serializers.ValidationError(_("Capability ok flag must be a boolean"))
        return value

    def get_unique_together_validators(self):
        # 条件唯一约束（is_active=True 部分索引）会生成 UniqueTogetherValidator 把
        # is_active 误判必填；激活唯一性由 service 层 set_active_profile + DB 索引兜底
        return []

    def validate_name(self, value):
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError(_("Profile name is required"))
        return name

    def validate_base_url(self, value):
        url = (value or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise serializers.ValidationError(_("Base URL must start with http:// or https://"))
        return url

    def create(self, validated_data):
        validated_data["api_key"] = _encrypt_api_key(validated_data.get("api_key", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", "")
        if api_key.strip():
            # 留空 = 沿用原密钥
            validated_data["api_key"] = _encrypt_api_key(api_key)
        return super().update(instance, validated_data)


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
