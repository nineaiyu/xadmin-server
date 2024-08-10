#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : file
# author : ly_13
# date : 7/24/2024
import logging

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from system.models import UploadFile
from system.serializers.upload import UploadFileSerializer

logger = logging.getLogger(__name__)


class UploadFileFilter(BaseFilterSet):
    filename = filters.CharFilter(field_name='filename', lookup_expr='icontains')

    class Meta:
        model = UploadFile
        fields = ['filename', 'mime_type', 'md5sum', 'description', 'is_upload', 'is_tmp']


class UploadFileView(BaseModelSet):
    """
    文件管理
    """
    queryset = UploadFile.objects.all()
    serializer_class = UploadFileSerializer
    ordering_fields = ['created_time', 'filesize']
    filterset_class = UploadFileFilter
