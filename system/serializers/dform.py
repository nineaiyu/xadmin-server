#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单序列化器。定义类资源豁免字段权限裁剪。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.utils.dform import validate_draft_data, validate_schema, validate_submission_data


class ApprovalFlowRelatedField(BasePrimaryKeyRelatedField):
    """审批流程外键：取值域不做行级数据权限过滤（定义类资源，与表单定义同口径）。"""

    def get_queryset(self):
        from system.models.approval import ApprovalFlow

        return ApprovalFlow.objects.all()


class DynamicFormSerializer(BaseModelSerializer):
    ignore_field_permission = True

    # 绑定流程后提交进入流程引擎（多级审批），approval_required 的操作审批被忽略
    approval_flow = ApprovalFlowRelatedField(
        required=False, allow_null=True, attrs=["pk", "name"], format="{name}", label=_("Approval flow")
    )

    class Meta:
        model = DynamicForm
        # approval_required：开启后提交走敏感操作审批（412 → 通过 → 携令牌重放）
        fields = [
            "pk",
            "name",
            "description",
            "schema",
            "is_active",
            "approval_required",
            "approval_flow",
            "is_template",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "created_time", "updated_time"]
        # RePlusPage 列表列：schema 列由前端渲染「字段数」（不直接展示 JSON）
        table_fields = [
            "name",
            "schema",
            "approval_flow",
            "is_active",
            "approval_required",
            "description",
            "updated_time",
        ]

    def validate_schema(self, value):
        validate_schema(value if isinstance(value, dict) else {})
        return value

    def validate(self, attrs):
        """模板约束：创建后不可改模板标记；模板不绑定审批流程（只做 schema 复用）。"""
        if self.instance is not None:
            requested = attrs.get("is_template", self.instance.is_template)
            if bool(requested) != bool(self.instance.is_template):
                raise serializers.ValidationError(_("Is template cannot be changed after creation"))
        is_template = attrs.get("is_template", getattr(self.instance, "is_template", False))
        if is_template and attrs.get("approval_flow"):
            raise serializers.ValidationError(_("A form template cannot bind an approval flow"))
        return attrs


class FormPkField(serializers.PrimaryKeyRelatedField):
    """表单外键取值域不做行级数据权限过滤（定义类资源）。"""

    def get_queryset(self):
        return DynamicForm.objects.all()


class DynamicFormSubmissionSerializer(BaseModelSerializer):
    ignore_field_permission = True
    form_name = serializers.CharField(source="form.name", read_only=True)
    # 覆盖 BaseModelSerializer 默认的 BasePrimaryKeyRelatedField（其取值域
    # 会做数据权限过滤，普通用户无授权即"对象不存在"）
    form = FormPkField(queryset=DynamicForm.objects.all())
    # 状态由审批结果驱动（绑定流程时随实例终态回写），空 = 无需审批已生效
    status = LabeledChoiceField(choices=DynamicFormSubmission.Status.choices, required=False, read_only=True)
    # 表单 schema 快照：详情页渲染字段 label / 复杂控件展示（填报页无需表单设计器权限）
    form_schema = serializers.SerializerMethodField(label=_("Form schema"))
    # 审批轨迹：绑定流程的提交按实例任务读（状态口径与流程审批中心同源）
    approval_trail = serializers.SerializerMethodField(label=_("Approval trail"))

    class Meta:
        model = DynamicFormSubmission
        fields = [
            "pk",
            "form",
            "form_name",
            "form_schema",
            "data",
            "status",
            "instance",
            "approval_trail",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "creator", "created_time", "updated_time", "instance"]
        table_fields = ["form_name", "status", "creator", "created_time"]

    def get_form_schema(self, obj) -> list:
        return list((obj.form.schema or {}).get("fields") or []) if obj.form_id else []

    def get_approval_trail(self, obj) -> list:
        """审批轨迹：实例任务的展示口径（状态/审批人/意见/时间/加签/委托来源）。"""
        if not obj.instance_id:
            return []
        from system.serializers.approval_flow import ApprovalNodeTaskSerializer

        tasks = obj.instance.tasks.all()
        return ApprovalNodeTaskSerializer(tasks, many=True, context=self.context).data

    def validate(self, attrs):
        form = attrs.get("form") or getattr(self.instance, "form", None)
        if form is None:
            raise serializers.ValidationError(_("Dynamic form is required"))
        if form.is_template:
            raise serializers.ValidationError(_("A form template cannot accept submissions"))
        if not form.is_active:
            raise serializers.ValidationError(_("This form is no longer accepting submissions"))
        data = attrs.get("data")
        if data is None and self.instance:
            data = self.instance.data
        # 草稿（DRAFT）：轻校验（结构/体积），必填与取值在提交时按完整规则校验
        if self.context.get("draft"):
            attrs["data"] = validate_draft_data(data)
        else:
            # 提交侧校验：与 schema 定义同源（未知键/required/选项/边界；字典字段读字典值）
            attrs["data"] = validate_submission_data(form.schema, data)
        return attrs
