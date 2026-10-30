#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单（表单定义侧）序列化器：定义类资源豁免字段权限裁剪。

提交侧（提交/导出/表单数据）序列化器见 ``dform_submission.py``（自本文件拆出，
控制单文件体量）；两个导出仍保持 ``dataset.serializers.dform`` 的历史导入路径
（底部再导出），消费方无需感知拆分。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField
from common.core.serializers import BaseModelSerializer
from dataset.models.dform import MAX_SCHEMA_HISTORY, DynamicForm
from dataset.utils.dform import normalize_schema


class ApprovalFlowRelatedField(BasePrimaryKeyRelatedField):
    """审批流程外键：取值域不做行级数据权限过滤（定义类资源，与表单定义同口径）。"""

    def get_queryset(self) -> Any:
        from approval.contracts import ApprovalFlow

        return ApprovalFlow.objects.all()


class DynamicFormSerializer(BaseModelSerializer):
    ignore_field_permission = True
    # 非创建者修改/删除被写守卫拒绝（1003）：下发 is_owner 供行内按钮显隐
    row_owner_guard = True

    # 绑定流程后提交进入流程引擎（多级审批），approval_required 的操作审批被忽略
    approval_flow = ApprovalFlowRelatedField(
        required=False, allow_null=True, attrs=["pk", "name"], format="{name}", label=_("Approval flow")
    )

    # 乐观锁基线（write_only）：客户端原样回传 updated_time 渲染串，与当前行同款渲染后比对
    base_updated_time = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = DynamicForm
        # approval_required：开启后提交走敏感操作审批（412 → 通过 → 携令牌重放）
        fields = [
            "pk",
            "name",
            "description",
            "schema",
            "schema_version",
            "is_active",
            "approval_required",
            "approval_flow",
            "is_template",
            "base_updated_time",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "schema_version", "created_time", "updated_time"]
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

    def validate_schema(self, value: Any) -> Any:
        """写入侧规范化：字段 + 联动规则（未声明键丢弃，缺省不写入 linkages）。"""
        return normalize_schema(value if isinstance(value, dict) else {})

    def validate(self, attrs: Any) -> Any:
        """模板约束 + 乐观锁：base_updated_time 与库内版本不一致时拒绝后写覆盖。

        流程引用检查：绑定流程的表单删除「被流程引用」的字段 → 拒绝（改版后
        条件失效节点会被静默跳过，见 dform_flow.assert_schema_safe_for_flow）。
        """
        base_updated_time = attrs.pop("base_updated_time", None)
        if (
            base_updated_time
            and self.instance is not None
            and self.instance.updated_time is not None
            and str(base_updated_time) != serializers.DateTimeField().to_representation(self.instance.updated_time)
        ):
            raise serializers.ValidationError(
                {"base_updated_time": _("The form has been modified by another user; reload before saving")}
            )
        if self.instance is not None:
            requested = attrs.get("is_template", self.instance.is_template)
            if bool(requested) != bool(self.instance.is_template):
                raise serializers.ValidationError(_("Is template cannot be changed after creation"))
        is_template = attrs.get("is_template", getattr(self.instance, "is_template", False))
        if is_template and attrs.get("approval_flow"):
            raise serializers.ValidationError(_("A form template cannot bind an approval flow"))
        if self.instance is not None and attrs.get("schema") is not None:
            from dataset.utils.dform_flow import assert_schema_safe_for_flow

            assert_schema_safe_for_flow(self.instance, attrs["schema"])
        return attrs

    def create(self, validated_data: Any) -> Any:
        """新建后同步绑定流程的 form_schema 投影（绑定即投影，含创建时绑定）。"""
        from dataset.utils.dform_flow import sync_bound_flow_schema

        instance = super().create(validated_data)
        sync_bound_flow_schema(instance)
        return instance

    def update(self, instance: Any, validated_data: Any) -> Any:
        """schema 实质变更 → 版本 +1 并归档变更前快照（保留最近 MAX_SCHEMA_HISTORY 个）。

        同内容保存（规范化后相等）不产生新版本，避免「点一次保存就 +1」的噪声版本。
        绑定变化（换绑/解绑）后做流程侧 form_schema 再同步（单向投影）。
        """
        new_schema = validated_data.get("schema")
        if new_schema is not None and new_schema != (instance.schema or {}):
            request = self.context.get("request")
            user = getattr(request, "user", None)
            history = list(instance.schema_history or [])
            history.insert(
                0,
                {
                    "version": instance.schema_version or 1,
                    "schema": instance.schema or {},
                    "updated_time": instance.updated_time.isoformat() if instance.updated_time else "",
                    "updated_by": getattr(user, "username", "") or "",
                },
            )
            validated_data["schema_history"] = history[:MAX_SCHEMA_HISTORY]
            validated_data["schema_version"] = (instance.schema_version or 1) + 1
        previous_flow_id = instance.approval_flow_id
        instance = super().update(instance, validated_data)
        from dataset.utils.dform_flow import resync_flow_after_unbind, sync_bound_flow_schema

        if instance.approval_flow_id:
            sync_bound_flow_schema(instance)
        if previous_flow_id and previous_flow_id != instance.approval_flow_id:
            # 换绑 A→B：新流程已投影；旧流程仍需按剩余绑定表单重算，
            # 否则 A 会保留已脱离表单的字段（独立发起 A 时展示/校验漂移）。
            resync_flow_after_unbind(previous_flow_id)
        return instance


class DynamicFormListSerializer(BaseModelSerializer):
    """表单定义列表轻量序列化器：schema 裁剪为字段数（列表页只渲染字段数列）。

    schema 全文随行回传是设计器列表的主开销（大表单 × 每页行数），列表行只保留
    字段数元数据；需要 schema 全文的入口（设计器编辑弹窗、存为模板、从模板新建）
    经 retrieve 单条取全量（DynamicFormSerializer），与「我的填报」列表的拆分同口径。
    """

    ignore_field_permission = True
    # 与 DynamicFormSerializer 同口径：下发 is_owner 供列表行内按钮显隐
    row_owner_guard = True
    approval_flow = ApprovalFlowRelatedField(
        required=False, allow_null=True, attrs=["pk", "name"], format="{name}", label=_("Approval flow")
    )
    schema_fields_count = serializers.SerializerMethodField(label=_("Field count"))

    class Meta:
        model = DynamicForm
        fields = [
            "pk",
            "name",
            "description",
            "schema_fields_count",
            "schema_version",
            "is_active",
            "approval_required",
            "approval_flow",
            "is_template",
            "created_time",
            "updated_time",
        ]
        read_only_fields = fields

    def get_schema_fields_count(self, obj: Any) -> int:
        return len((obj.schema or {}).get("fields") or [])


# 提交侧序列化器自本文件拆出后保持历史导入路径（消费方与既有用例不感知拆分）
from .dform_submission import (  # noqa: E402,F401
    DynamicFormSubmissionSerializer as DynamicFormSubmissionSerializer,
)
from .dform_submission import (
    FormDataDetailSerializer as FormDataDetailSerializer,
)
from .dform_submission import (
    FormDataListSerializer as FormDataListSerializer,
)
from .dform_submission import (
    FormPkField as FormPkField,
)
from .dform_submission import (
    MySubmissionListSerializer as MySubmissionListSerializer,
)
from .dform_submission import (
    SubmissionDataField as SubmissionDataField,
)
from .dform_submission import (
    SubmissionExportSerializer as SubmissionExportSerializer,
)
from .dform_submission import (
    approval_trail_of as approval_trail_of,
)
from .dform_submission import (
    export_dynamic_fields as export_dynamic_fields,
)
from .dform_submission import (
    form_schema_of as form_schema_of,
)
