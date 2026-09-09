#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导入记录（下载中心）序列化器。

只读资源：全部字段 read_only；列表展示文件名/目标模块/动作/状态/行数统计/报告/触发人。
状态走数据字典 import_status（管理员可维护文案/颜色），未配置回退模型枚举。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.import_ import ImportRecord
from system.serializers.fields import DictChoiceField
from system.serializers.task import DisplayRelatedField


class ImportRecordSerializer(BaseModelSerializer):
    source_file = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Source file"), label_builder=lambda value: value.filename
    )
    error_report = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Error report"), label_builder=lambda value: value.filename
    )
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Creator"))
    report_filesize = serializers.SerializerMethodField(label=_("Report size"))
    status = DictChoiceField(dict_code="import_status", fallback_choices=ImportRecord.Status.choices, read_only=True)

    class Meta:
        model = ImportRecord
        fields = [
            "pk",
            "name",
            "module",
            "path",
            "action",
            "status",
            "total",
            "success_rows",
            "failed_rows",
            "source_file",
            "error_report",
            "report_filesize",
            "creator",
            "error",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "name",
            "module",
            "action",
            "status",
            "total",
            "success_rows",
            "failed_rows",
            "report_filesize",
            "error_report",
            "creator",
            "created_time",
        ]
        read_only_fields = fields

    def get_report_filesize(self, obj):
        return obj.report_filesize
