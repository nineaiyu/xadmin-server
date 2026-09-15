#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托序列化器（审批流三期）。

- 写入校验：委托人与代理人不得相同、结束时间晚于开始时间、
  同一委托人不得存在时间重叠的生效委托（解析歧义兜底）；
- 列表展示：委托人/代理人用户名与昵称、流程范围（空 = 全部流程）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models import ApprovalDelegation


class ApprovalDelegationSerializer(BaseModelSerializer):
    delegator_name = serializers.CharField(source="delegator.username", read_only=True)
    delegate_name = serializers.CharField(source="delegate.username", read_only=True)
    delegate_nickname = serializers.CharField(source="delegate.nickname", read_only=True)

    class Meta:
        model = ApprovalDelegation
        fields = [
            "pk",
            "delegator",
            "delegator_name",
            "delegate",
            "delegate_name",
            "delegate_nickname",
            "start_time",
            "end_time",
            "flow_codes",
            "is_active",
            "remark",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["creator", "created_time", "updated_time"]
        table_fields = [
            "delegator_name",
            "delegate_name",
            "start_time",
            "end_time",
            "flow_codes",
            "is_active",
            "remark",
        ]

    def validate(self, attrs):
        delegator = attrs.get("delegator") or getattr(self.instance, "delegator", None)
        delegate = attrs.get("delegate") or getattr(self.instance, "delegate", None)
        start = attrs.get("start_time") or getattr(self.instance, "start_time", None)
        end = attrs.get("end_time") or getattr(self.instance, "end_time", None)
        if delegator and delegate and delegator.pk == delegate.pk:
            raise serializers.ValidationError({"delegate": _("Delegator and delegate cannot be the same person")})
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": _("End time must be later than start time")})
        # 同一委托人时间重叠的生效委托：解析会取「最新一条」，重叠即歧义，直接拒绝
        if delegator and start and end:
            conflict = ApprovalDelegation.objects.filter(
                delegator=delegator,
                is_active=True,
                start_time__lt=end,
                end_time__gt=start,
            )
            if self.instance is not None:
                conflict = conflict.exclude(pk=self.instance.pk)
            if conflict.exists():
                raise serializers.ValidationError({"detail": _("An overlapping active delegation already exists")})
        return attrs
