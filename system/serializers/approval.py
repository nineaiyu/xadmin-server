#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批单序列化器。

只读资源：记录由审批链路推进（建单/通过/驳回/撤回/消费均走 action），全部字段
read_only；params 为脱敏后请求体快照，仅供审批人核对。

多级审批链（命中 ApprovalRule 时）在列表暴露三个只读投影字段：
- current_level：当前第几级（0 = 扁平单或已结束）；
- current_assignees：当前级候选人（列表直接显示「现在该谁审」）；
- can_act：当前用户是否可处理该单（行内通过/驳回按钮的可见性收口）。
详情（retrieve）额外返回 steps：逐级审批进度与留痕。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.fields import DictChoiceField
from common.core.serializers import BaseModelSerializer
from system.models.approval import ApprovalRequest
from system.serializers.task import DisplayRelatedField
from system.utils.approval import can_act


class ApprovalRequestSerializer(BaseModelSerializer):
    creator = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Applicant"), label_builder=lambda value: value.username
    )
    approver = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Approver"), label_builder=lambda value: value.username
    )
    # 处理人显示名快照（用户删除/改名后审批痕迹仍可读）
    approver_display = serializers.CharField(read_only=True, label=_("Approver display"))
    # 状态标签字典化：文案/颜色管理员可在数据字典 approval_status 维护（默认项随种子下发），
    # 字典未配置时回退模型枚举（merge 保证只配部分项时其余枚举标签不缺）
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalRequest.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    current_level = serializers.SerializerMethodField(label=_("Current level"))
    current_assignees = serializers.SerializerMethodField(label=_("Current approvers"))
    can_act = serializers.SerializerMethodField(label=_("Can approve"))

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
            "approver_display",
            "approved_at",
            "expired_at",
            "consume_time",
            "reason",
            "creator",
            "current_level",
            "current_assignees",
            "can_act",
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
            "current_level",
            "current_assignees",
            "reason",
            "creator",
            "created_time",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField)
    def get_current_level(self, obj) -> int:
        return obj.current_level or 0

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_current_assignees(self, obj) -> list:
        if not (obj.current_level or 0):
            return []
        return [{"pk": user.pk, "username": user.username} for user in obj.current_assignees.all()]

    @extend_schema_field(serializers.BooleanField)
    def get_can_act(self, obj) -> bool:
        request = self.context.get("request")
        return can_act(obj, getattr(request, "user", None))


class ApprovalRequestDetailSerializer(ApprovalRequestSerializer):
    """审批单详情：额外返回 steps（多级审批链的逐级进度与留痕）+ target_snapshot。"""

    steps = serializers.SerializerMethodField(label=_("Approval steps"))
    # 目标对象轻量快照（变更前后事实对照；缺失时为空 dict，前端降级展示）
    target_snapshot = serializers.JSONField(read_only=True, label=_("Target snapshot"))

    class Meta(ApprovalRequestSerializer.Meta):
        fields = ApprovalRequestSerializer.Meta.fields + ["steps", "target_snapshot"]
        table_fields = ApprovalRequestSerializer.Meta.table_fields
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_steps(self, obj) -> list:
        queryset = (
            obj.steps.select_related("approver").prefetch_related("assignees", "actions__approver").order_by("order")
        )
        steps = []
        for step in queryset:
            actions = list(step.actions.all())
            steps.append(
                {
                    "order": step.order,
                    "name": step.name,
                    # 审批方式（OR 或签 / AND 会签）+ 会签进度（已通过 n 人）
                    "approve_type": step.approve_type,
                    "approved_count": len([item for item in actions if item.status == "APPROVED"]),
                    "assignee_type": step.assignee_type,
                    "assignee_value": step.assignee_value,
                    "assignees": [{"pk": user.pk, "username": user.username} for user in step.assignees.all()],
                    "status": step.status,
                    "approver": (
                        {
                            "pk": step.approver.pk,
                            "username": step.approver.username,
                            # 显示名快照优先（用户删除/改名后留痕不降级）
                            "display": step.approver_display or step.approver.username,
                        }
                        if step.approver
                        else None
                    ),
                    "actions": [
                        {
                            "approver": (
                                {
                                    "pk": item.approver.pk,
                                    "username": item.approver.username,
                                    "display": item.approver_display or item.approver.username,
                                }
                                if item.approver
                                else None
                            ),
                            "status": item.status,
                            "comment": item.comment,
                            "created_time": item.created_time,
                        }
                        for item in actions
                    ],
                    "comment": step.comment,
                    "acted_at": step.acted_at,
                }
            )
        return steps
