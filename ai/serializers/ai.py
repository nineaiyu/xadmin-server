#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库序列化器：文档列表/预览 + 上传写入校验。

- 列表轻量（不含全文，to_representation 按 action 裁剪）；
- retrieve 附分块摘要（chunks）供前端预览「问答时会切成哪些块」；
- 上传写入走独立输入序列化器（name + content 文本），不落文件系统。
"""

from django.db.models.functions import Left, Length
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from ai.models.ai import AiKnowledgeChunk, AiKnowledgeDocument, AiProfile
from ai.utils.ai import MAX_UPLOAD_CONTENT_LENGTH, MAX_UPLOAD_NAME_LENGTH, set_document_active
from ai.utils.ai_config import outbound_allowed_hosts
from ai.utils.doc_extract import PARSABLE_EXTENSIONS
from common.base.utils import signer
from common.core.serializers import BaseModelSerializer
from common.core.validation import trim_required
from common.utils.outbound import OutboundBlocked, validate_outbound_config_url
from task.services import DisplayRelatedField

# 名称中的路径分隔符会破坏 upload/ 前缀隔离，统一拒绝
NAME_FORBIDDEN_CHARS = ("/", "\\")
# 分块摘要预览只取开头若干字符：DB 侧裁剪，大文本 content 不整段出库
CHUNK_PREVIEW_LENGTH = 120


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
        return trim_required(value, _("Profile name is required"))

    def validate_base_url(self, value):
        url = (value or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise serializers.ValidationError(_("Base URL must start with http:// or https://"))
        # 出站地址归属校验（SSRF），与 Webhook/MCP 同口径：https 强制（写入侧域名不解
        # 析，发送侧严格校验）；http 仅允许 loopback（本地联调）或 OUTBOUND_ALLOWED_HOSTS
        # 登记目标——自建推理服务须显式登记主机，base_url 默认不得指向内网
        try:
            validate_outbound_config_url(
                url,
                allowed_hosts=outbound_allowed_hosts(),
                scheme_message=_(
                    "Base URL must use https (http is allowed for loopback or OUTBOUND_ALLOWED_HOSTS targets)"
                ),
            )
        except OutboundBlocked as exc:
            raise serializers.ValidationError([str(item) for item in exc.messages]) from exc
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
    """上传写入：name 唯一（同名覆盖更新）+ content 全文文本。

    二值载荷（7.3 文档解析扩展）：``file_type`` + ``file_b64`` 传 PDF/DOCX 原文
    （base64），写入侧解析为纯文本后作为 content 落库（检索/分块链路零改动）。
    二选一：文本直传（content）或二进制解析（file_type + file_b64）。
    """

    name = serializers.CharField(max_length=MAX_UPLOAD_NAME_LENGTH)
    content = serializers.CharField(
        max_length=MAX_UPLOAD_CONTENT_LENGTH, required=False, allow_blank=True, allow_null=True
    )
    file_type = serializers.ChoiceField(choices=sorted(PARSABLE_EXTENSIONS), required=False)
    # 2MB 二进制 ≈ 2.74M base64 字符：字段级上限即体积门（解码后仍二次校验）
    file_b64 = serializers.CharField(max_length=2_800_000, required=False, allow_blank=True, write_only=True)

    def validate_name(self, value):
        name = trim_required(value, _("Document name is required"))
        if any(char in name for char in NAME_FORBIDDEN_CHARS) or ".." in name:
            raise serializers.ValidationError(_("Document name cannot contain path characters"))
        return name

    def validate_content(self, value):
        if value and not (value or "").strip():
            raise serializers.ValidationError(_("Document content cannot be empty"))
        return value

    def validate(self, attrs):
        file_type = attrs.get("file_type")
        file_b64 = attrs.get("file_b64")
        content = attrs.get("content")
        if file_type or file_b64:
            if not (file_type and file_b64):
                raise serializers.ValidationError(_("Both file_type and file_b64 are required"))
            attrs["content"] = self._extract(file_type, file_b64)
        elif not (content or "").strip():
            raise serializers.ValidationError(_("Provide either the document content or a pdf/docx file"))
        attrs.pop("file_type", None)
        attrs.pop("file_b64", None)
        return attrs

    @staticmethod
    def _extract(file_type: str, file_b64: str) -> str:
        """base64 → bytes → 纯文本（doc_extract；含体积与空文本门）。"""
        import base64
        import binascii

        from ai.utils.doc_extract import extract_text

        try:
            raw = base64.b64decode(file_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise serializers.ValidationError(_("The uploaded file payload is invalid")) from exc
        text = extract_text(str(file_type), raw)
        if len(text) > MAX_UPLOAD_CONTENT_LENGTH:
            raise serializers.ValidationError(
                _("The extracted text exceeds the max length {}").format(MAX_UPLOAD_CONTENT_LENGTH)
            )
        return text


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
        """分块摘要（仅详情返回）：问答检索时会命中的块清单。

        预览在 DB 侧裁剪（Left/Length）：只把开头 120 字符与全文长度取回，
        避免 content 大文本整段出库才截断。
        """
        if getattr(self.context.get("view"), "action", None) != "retrieve":
            return []
        rows = (
            AiKnowledgeChunk.objects.filter(source_path=obj.path)
            .order_by("chunk_index")
            .values("chunk_index", size=Length("content"), preview=Left("content", CHUNK_PREVIEW_LENGTH))
        )
        return [{"index": row["chunk_index"], "size": row["size"], "preview": row["preview"]} for row in rows]

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
