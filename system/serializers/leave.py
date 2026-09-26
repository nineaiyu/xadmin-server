#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""请假业务单序列化器。

- leave_type / status 走数据字典（leave_type / leave_status），字典未配置时回退模型
  choices（merge_fallback，避免字典只配了部分项导致写入被拒）；
- 审批侧只读字段（instance_pk / instance_no / current_node_name / reject_reason /
  finished_at）来自绑定的流程实例，详情页据此展示审批进度，前端按 instance_pk 拉
  审批轨迹（复用流程实例接口，不重复造数据结构）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import DictChoiceField
from common.core.serializers import BaseModelSerializer
from system.models.leave import Leave
from system.serializers.task import DisplayRelatedField
from system.utils.leave import leave_days, validate_leave_payload


def _username(value):
    return getattr(value, "username", str(value))


class LeaveSerializer(BaseModelSerializer):
    # 业务单由普通用户自助提交（creator 隔离），不做字段级权限裁剪/脱敏：
    # 无 FieldPermission 配置时非超管会被裁成空字段集（历史坑，dform/dataset 同款处理）
    ignore_field_permission = True
    leave_type = DictChoiceField(
        dict_code="leave_type", fallback_choices=Leave.LeaveType.choices, merge_fallback=True, label=_("Leave type")
    )
    status = DictChoiceField(
        dict_code="leave_status",
        fallback_choices=Leave.Status.choices,
        merge_fallback=True,
        read_only=True,
        label=_("Status"),
    )
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Applicant"), label_builder=_username)
    instance_pk = serializers.SerializerMethodField(label=_("Approval instance"))
    instance_no = serializers.SerializerMethodField(label=_("Application no"))
    current_node_name = serializers.SerializerMethodField(label=_("Current node"))
    reject_reason = serializers.SerializerMethodField(label=_("Reject reason"))
    finished_at = serializers.SerializerMethodField(label=_("Finished at"))

    class Meta:
        model = Leave
        fields = [
            "pk",
            "leave_type",
            "start_date",
            "end_date",
            "days",
            "reason",
            "status",
            "instance_pk",
            "instance_no",
            "current_node_name",
            "reject_reason",
            "finished_at",
            "creator",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "leave_type",
            "start_date",
            "end_date",
            "days",
            "reason",
            "status",
            "current_node_name",
            "creator",
            "created_time",
        ]
        read_only_fields = [
            "pk",
            "status",
            "instance_pk",
            "instance_no",
            "current_node_name",
            "reject_reason",
            "finished_at",
            "creator",
            "created_time",
            "updated_time",
        ]

    # ---------------------------------------------------------------- 只读派生字段

    def get_instance_pk(self, obj):
        return str(obj.instance_id) if obj.instance_id else ""

    def get_instance_no(self, obj) -> str:
        return str(obj.instance_id)[:8].upper() if obj.instance_id else ""

    def get_current_node_name(self, obj) -> str:
        instance = obj.instance
        if instance is None or obj.status != Leave.Status.PENDING:
            return ""
        return getattr(instance.current_node, "name", "") or ""

    def get_reject_reason(self, obj) -> str:
        instance = obj.instance
        return (getattr(instance, "reason", "") or "") if instance is not None else ""

    def get_finished_at(self, obj):
        instance = obj.instance
        return getattr(instance, "finished_at", None) if instance is not None else None

    # ---------------------------------------------------------------- 校验与写入

    def validate(self, attrs):
        start_date = attrs.get("start_date") or getattr(self.instance, "start_date", None)
        end_date = attrs.get("end_date") or getattr(self.instance, "end_date", None)
        days = attrs.get("days")
        if days in (None, "") and self.instance is None:
            days = leave_days(start_date, end_date)
        if days in (None, ""):
            days = getattr(self.instance, "days", None)

        if self.instance is not None and self.instance.status in (Leave.Status.PENDING, Leave.Status.APPROVED):
            raise serializers.ValidationError(
                {"status": _("Requests in approval or already approved cannot be modified")}
            )
        request = self.context.get("request")
        user = getattr(request, "user", None)
        error = validate_leave_payload(
            start_date=start_date,
            end_date=end_date,
            days=days,
            creator=getattr(self.instance, "creator", None) or user,
            exclude_pk=getattr(self.instance, "pk", None),
        )
        if error:
            raise serializers.ValidationError({"detail": error})
        attrs["days"] = days
        return attrs
