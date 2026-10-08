#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托序列化器（审批流三期）。

- 写入校验：委托人与代理人不得相同、代理人必须为启用用户（停用代理人会被解析
  丢弃，节点候选可能因此清空而被「自动通过」放行）、结束时间晚于开始时间、
  同一委托人不得存在时间重叠的生效委托（重叠校验按本行落库后的 is_active 收口，
  未启用的委托模板允许先保存）；
- 列表展示：委托人/代理人用户名与昵称、流程范围（空 = 全部流程）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from approval.models import ApprovalDelegation
from common.core.serializers import BaseModelSerializer


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
        # 委托人/代理人统一用户选择形态（label = 昵称(用户名)，与全局用户展示一致）；
        # 代理人是否升级为远程联想由 ViewSet 的 suggestion_fields 白名单声明，
        # 委托人未入名单，保持 api-search-user 弹窗选择器。
        extra_kwargs = {
            "delegator": {
                "attrs": ["pk", "username", "nickname"],
                "format": "{nickname}({username})",
                "input_type": "api-search-user",
            },
            "delegate": {
                "attrs": ["pk", "username", "nickname"],
                "format": "{nickname}({username})",
                "input_type": "api-search-user",
            },
        }
        table_fields = [
            "delegator_name",
            "delegate_name",
            "start_time",
            "end_time",
            "flow_codes",
            "is_active",
            "remark",
        ]

    def _check_delegator_ownership(self, delegator):
        """委托归属护栏：非超管只能以自己的名义创建/维护委托。

        生效委托在节点解析时会直接替换「待办归属」（引擎只判行存在，不复查是谁建的），
        若能以他人名义建委托，等于收编他人的全部待办，属横向越权；这里 fail-closed 拒绝。
        """
        request = getattr(self, "request", None)
        user = getattr(request, "user", None) if request is not None else None
        if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
            return
        if delegator is not None and delegator.pk != user.pk:
            raise serializers.ValidationError({"delegator": _("Delegations can only be created in your own name")})

    def validate(self, attrs):
        delegator = attrs.get("delegator") or getattr(self.instance, "delegator", None)
        delegate = attrs.get("delegate") or getattr(self.instance, "delegate", None)
        start = attrs.get("start_time") or getattr(self.instance, "start_time", None)
        end = attrs.get("end_time") or getattr(self.instance, "end_time", None)
        # 重叠校验按「本行落库后是否生效」收口：未启用的委托模板允许先保存
        # （既有行侧冲突查询本就只看 is_active=True，本行是否参与由自身 is_active 决定）
        row_active = attrs["is_active"] if "is_active" in attrs else getattr(self.instance, "is_active", True)
        self._check_delegator_ownership(delegator)
        if delegator and delegate and delegator.pk == delegate.pk:
            raise serializers.ValidationError({"delegate": _("Delegator and delegate cannot be the same person")})
        if delegate is not None and not delegate.is_active:
            raise serializers.ValidationError({"delegate": _("Delegate must be an active user")})
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": _("End time must be later than start time")})
        # 流程范围 fail-closed：码必须是真实存在的启用流程——引擎按 code 匹配流程，
        # 填错码的委托会静默不生效（表面建了委托，实际从不替换待办）
        flow_codes = attrs.get("flow_codes")
        if flow_codes is None and self.instance is not None:
            flow_codes = self.instance.flow_codes
        if flow_codes:
            from approval.models import ApprovalFlow

            valid = set(ApprovalFlow.objects.filter(code__in=flow_codes, is_active=True).values_list("code", flat=True))
            unknown = [code for code in flow_codes if code not in valid]
            if unknown:
                raise serializers.ValidationError(
                    {"flow_codes": _("Unknown approval flow code: %(codes)s") % {"codes": ", ".join(unknown)}}
                )
        # 同一委托人时间重叠的生效委托：解析会取「最新一条」，重叠即歧义，直接拒绝
        if delegator and start and end and row_active:
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
