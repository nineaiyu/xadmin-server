#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : upload
# author : ly_13
# date : 8/10/2024

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.serializers import BaseModelSerializer
from common.fields.utils import get_file_absolute_uri
from common.utils import get_logger
from system.models import UploadFile
from system.serializers.fields import DictChoiceField
from system.utils.preview import preview_kind

logger = get_logger(__name__)


class UploadFileSerializer(BaseModelSerializer):
    # 分类选项来自数据字典 upload_category：字典约束写入路径（非法值 invalid_choice），
    # 管理员改字典即时生效；无回退枚举（分类是纯管理口径，无历史值兼容问题）
    category = DictChoiceField(
        dict_code="upload_category",
        required=False,
        allow_null=True,
        label=_("Category"),
    )

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
            "deleted_at",
            "preview_kind",
        ]
        read_only_fields = ["pk", "is_upload", "deleted_at", "preview_kind"]
        table_fields = [
            "pk",
            "filename",
            "filesize",
            "mime_type",
            "category",
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
    def get_preview_kind(self, obj):
        if not obj.filepath:
            return None
        return preview_kind(obj)

    @extend_schema_field(serializers.CharField)
    def get_access_url(self, obj):
        return obj.file_url if obj.file_url else get_file_absolute_uri(obj.filepath, self.context.get("request", None))

    def create(self, validated_data):
        if not validated_data.get("file_url"):
            raise ValidationError(_("Internet url cannot be null"))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if not validated_data.get("file_url") and not instance.is_upload:
            raise ValidationError("Internet url cannot be null")
        return super().update(instance, validated_data)
