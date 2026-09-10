#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批单序列化器。

只读资源：记录由审批链路推进（建单/通过/驳回/撤回/消费均走 action），全部字段
read_only；params 为脱敏后请求体快照，仅供审批人核对。
"""

from django.utils.translation import gettext_lazy as _

from common.core.serializers import BaseModelSerializer
from system.models.approval import ApprovalRequest
from system.serializers.fields import DictChoiceField
from system.serializers.task import DisplayRelatedField


class ApprovalRequestSerializer(BaseModelSerializer):
    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Applicant"), label_builder=lambda value: value.username
    )
    approver = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Approver"), label_builder=lambda value: value.username
    )
    # 状态标签字典化：文案/颜色管理员可在数据字典 approval_status 维护（默认项随种子下发），
    # 字典未配置时回退模型枚举（merge 保证只配部分项时其余枚举标签不缺）
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalRequest.Status.choices,
        merge_fallback=True,
        read_only=True,
    )

    class Meta:
        model = ApprovalRequest
        fields = [
            "pk",
            "module",
            "method",
            "path",
            "object_pk",
            "params",
            "status",
            "approver",
            "approved_at",
            "expired_at",
            "consume_time",
            "reason",
            "creator",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "module",
            "method",
            "path",
            "object_pk",
            "status",
            "approver",
            "reason",
            "creator",
            "created_time",
        ]
        read_only_fields = fields
