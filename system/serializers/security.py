#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件访问记录序列化器（file 域；暂留 system，随 file 域切分迁移）。"""

from common.core.fields import LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import FileAccessLog


class FileAccessLogSerializer(BaseModelSerializer):
    """文件访问记录（只读，详情页页签用）。"""

    action = LabeledChoiceField(choices=FileAccessLog.Action.choices, required=False)

    class Meta:
        model = FileAccessLog
        fields = ["pk", "filename", "user", "user_display", "action", "ipaddress", "result", "detail", "created_time"]
        read_only_fields = fields
        table_fields = ["user_display", "action", "ipaddress", "result", "created_time"]
