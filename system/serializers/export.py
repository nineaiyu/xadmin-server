#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导出记录（下载中心）序列化器。

只读资源：全部字段 read_only，列表展示文件名/来源模块/格式/状态/行数/大小/触发人。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.export import ExportRecord
from system.serializers.fields import DictChoiceField
from system.serializers.task import DisplayRelatedField


class ExportRecordSerializer(BaseModelSerializer):
    # 关联字段展示标签化：文件列显示文件名而非裸主键
    file = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Export file"), label_builder=lambda value: value.filename
    )
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Creator"))
    filesize = serializers.SerializerMethodField(label=_("File size"))
    # 状态标签字典化：管理员可在数据字典 export_status 维护文案/颜色（默认项随种子下发），
    # 字典未配置/项被清时回退模型枚举；merge 保证只配部分项时其余枚举标签不缺
    status = DictChoiceField(
        dict_code="export_status",
        fallback_choices=ExportRecord.Status.choices,
        merge_fallback=True,
        read_only=True,
    )

    class Meta:
        model = ExportRecord
        fields = [
            "pk",
            "name",
            "module",
            "path",
            "file_format",
            "status",
            "progress",
            "rows",
            "filesize",
            "file",
            "creator",
            "error",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "name",
            "module",
            "file_format",
            "status",
            "progress",
            "rows",
            "filesize",
            "creator",
            "created_time",
        ]
        read_only_fields = fields

    def get_filesize(self, obj):
        return obj.filesize
