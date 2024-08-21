#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : upload
# author : ly_13
# date : 8/10/2024

import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.serializers import BaseModelSerializer
from common.fields.utils import get_file_absolute_uri
from system.models import UploadFile

logger = logging.getLogger(__name__)


class UploadFileSerializer(BaseModelSerializer):
    class Meta:
        model = UploadFile
        fields = ['pk', 'filename', 'filesize', 'mime_type', 'md5sum', 'file_url', 'access_url', 'is_tmp', 'is_upload']
        read_only_fields = ["pk", "is_upload"]
        table_fields = ['pk', 'filename', 'filesize', 'mime_type', 'access_url', 'is_tmp', 'is_upload', 'md5sum']

    access_url = serializers.SerializerMethodField(label=_("Access URL"))

    @extend_schema_field(serializers.CharField)
    def get_access_url(self, obj):
        return obj.file_url if obj.file_url else get_file_absolute_uri(obj.filepath, self.context.get('request', None))

    def create(self, validated_data):
        if not validated_data.get('file_url'):
            raise ValidationError(_("Internet url cannot be null"))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if not validated_data.get('file_url') and not instance.is_upload:
            raise ValidationError('Internet url cannot be null')
        return super().update(instance, validated_data)
