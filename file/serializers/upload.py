#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : upload
# author : ly_13
# date : 8/10/2024

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.fields import DictChoiceField
from common.core.serializers import BaseModelSerializer
from common.fields.utils import get_file_absolute_uri
from common.utils import get_logger
from file.models import UploadFile
from file.utils.preview import preview_kind
from system.services import TaggedObjectSerializerMixin

logger = get_logger(__name__)


class UploadFileSerializer(TaggedObjectSerializerMixin, BaseModelSerializer):
    # 分类选项来自数据字典 upload_category：字典约束写入路径（非法值 invalid_choice），
    # 管理员改字典即时生效；无回退枚举（分类是纯管理口径，无历史值兼容问题）
    category = DictChoiceField(
        dict_code="upload_category",
        required=False,
        allow_null=True,
        label=_("Category"),
    )
    # 通用标签：只读回显（打标走 /api/system/tags/assign）
    tags = serializers.SerializerMethodField(label=_("Tags"))

    class Meta:
        model = UploadFile
        fields = [
            "pk",
            "filename",
            "filesize",
            "mime_type",
            "md5sum",
            "category",
            "file_url",
            "access_url",
            "is_tmp",
            "is_upload",
            "tags",
            "deleted_at",
            "preview_kind",
        ]
        # 上传内核写入的物理属性一律只读：客户端改写会绕过配额（filesize）或触发清理（is_tmp）
        read_only_fields = [
            "pk",
            "filename",
            "filesize",
            "mime_type",
            "md5sum",
            "is_tmp",
            "is_upload",
            "deleted_at",
            "preview_kind",
        ]
        table_fields = [
            "pk",
            "filename",
            "filesize",
            "mime_type",
            "category",
            "tags",
            "access_url",
            "is_tmp",
            "is_upload",
            "md5sum",
            "preview_kind",
        ]

    access_url = serializers.SerializerMethodField(label=_("Access URL"))
    # 预览类型由后端判定并下发：前端据此禁用不支持类型的预览按钮，
    # 避免前后端各判一次 mime（历史上这类"两端规则"必然漂移）
    preview_kind = serializers.SerializerMethodField(label=_("Preview type"))

    @extend_schema_field(serializers.CharField)
    def get_preview_kind(self, obj: Any) -> str | None:
        if not obj.filepath:
            return None
        return preview_kind(obj)

    @extend_schema_field(serializers.CharField)
    def get_access_url(self, obj: Any) -> Any:
        return obj.file_url if obj.file_url else get_file_absolute_uri(obj.filepath, self.context.get("request", None))

    @staticmethod
    def _clean_file_url(value: Any) -> str:
        from urllib.parse import urlsplit

        text = str(value or "").strip()
        if not text:
            return ""
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValidationError(_("File URL must be an http(s) address"))
        return text

    def create(self, validated_data: dict[str, Any]) -> Any:
        url = self._clean_file_url(validated_data.get("file_url"))
        if not url:
            raise ValidationError(_("Internet url cannot be null"))
        validated_data["file_url"] = url
        return super().create(validated_data)

    def update(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        if "file_url" in validated_data:
            validated_data["file_url"] = self._clean_file_url(validated_data["file_url"])
        effective = validated_data.get("file_url", instance.file_url)
        if not effective and not instance.is_upload:
            raise ValidationError(_("Internet url cannot be null"))
        return super().update(instance, validated_data)
