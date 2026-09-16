#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单视图。

- DynamicFormViewSet（管理员）：表单定义 CRUD（schema 写入侧校验）；
- DynamicFormSubmissionViewSet：填报与本人提交管理——普通用户按 creator 隔离
  （只见/只改本人提交），超管全量；停用表单拒新提交（序列化器校验）。

定义/提交类资源不做行级数据权限过滤（与 Dataset 同款处理）。
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, OnlyExportDataAction
from common.core.response import ApiResponse
from common.core.serializers import BaseModelSerializer
from common.swagger.utils import get_default_response_schema
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.serializers.dform import DynamicFormSerializer, DynamicFormSubmissionSerializer
from system.utils.dform_flow import create_flow_instance, resubmit_submission

_EDIT_DENY = _("Only the creator can modify a submission")
_PENDING_DENY = _("The submission is in approval and cannot be modified")


class SubmissionDataField(serializers.Field):
    """动态表单提交的「数据列」字段：从 instance.data 按 key 取值（导出展示口径）。

    导出列集合由视图在导出前按涉及表单的 schema 注入（`dynamic_fields` 上下文），
    未在 schema 声明的历史 data 键不导出（表单已删除字段的旧值不再出现在表头）。
    """

    def __init__(self, data_key, **kwargs):
        self.data_key = data_key
        super().__init__(**kwargs)

    def get_attribute(self, instance):
        return (instance.data or {}).get(self.data_key)

    def to_representation(self, value):
        return value


class SubmissionExportSerializer(BaseModelSerializer):
    """动态表单提交导出序列化器（C2）：固定列 + 按表单 schema 展开的动态数据列。

    复用导出框架（export-data / 渲染器按 `Meta.model` 定文件名、按 fields 出列）；
    动态字段在 `__init__` 末尾注入（绕开字段权限裁剪：导出列由 schema 决定）。
    """

    form_name = serializers.SerializerMethodField(label=_("Form"))
    creator_name = serializers.SerializerMethodField(label=_("Creator"))

    class Meta:
        model = DynamicFormSubmission
        fields = ["pk", "form_name", "creator_name", "created_time"]
        table_fields = ["form_name", "creator_name", "created_time"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in self.context.get("dynamic_fields") or []:
            # required=False：导出列标题不带 *（required 标记只对导入模板有意义）
            field = SubmissionDataField(data_key=key, label=label, required=False)
            # 手动绑定 field_name：渲染器按 field.field_name 取值与出列名
            field.field_name = key
            self.fields[key] = field

    def get_form_name(self, obj):
        return obj.form.name if obj.form_id else ""

    def get_creator_name(self, obj):
        return getattr(obj.creator, "username", "") or ""


class DynamicFormFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = DynamicForm
        fields = ["is_active", "approval_required"]


class SubmissionFilter(BaseFilterSet):
    class Meta:
        model = DynamicFormSubmission
        fields = ["form", "creator"]


class DynamicFormViewSet(BaseModelSet):
    """动态表单定义（管理员）"""

    queryset = DynamicForm.objects.all()
    serializer_class = DynamicFormSerializer
    ordering = ["-created_time"]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = DynamicFormFilter

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)


class DynamicFormSubmissionViewSet(BaseModelSet, OnlyExportDataAction):
    """动态表单提交（填报与本人提交管理；表单可挂审批流）。

    导出（C2）：`export-data` 复用导出框架，列 = 固定列（提交号/表单/提交人/时间）+
    按涉及表单 schema 展开的动态数据列；导出范围自动跟随列表筛选（含 creator 隔离）。
    """

    queryset = DynamicFormSubmission.objects.select_related("form", "creator")
    serializer_class = DynamicFormSubmissionSerializer
    # 导出专用序列化器（get_serializer_class 按 {action}_serializer_class 自动识别）
    export_data_serializer_class = SubmissionExportSerializer
    ordering = ["-created_time"]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = SubmissionFilter

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self, "action", None) == "export_data":
            context["dynamic_fields"] = self._export_dynamic_fields()
        return context

    def _export_dynamic_fields(self):
        """导出动态列：按过滤后行集合涉及的表单 schema 合并字段（key 去重、保留出现顺序）。"""
        queryset = self.filter_queryset(self.get_queryset())
        form_ids = list(queryset.values_list("form_id", flat=True).distinct())
        seen, fields_out = set(), []
        for form in DynamicForm.objects.filter(pk__in=form_ids):
            for item in (form.schema or {}).get("fields") or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                fields_out.append((key, str(item.get("label") or key)))
        return fields_out

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="available-forms")
    def available_forms(self, request, *args, **kwargs):
        """可填报表单（启用中）：填报页数据源。

        表单定义属定义类资源，取值域不做行级数据权限过滤——否则普通员工必须先被
        授予「表单设计器」的接口权限才能填报（定义与填报共用同一个列表接口的历史
        耦合），配置门槛高且语义不合理。
        """
        forms = DynamicForm.objects.filter(is_active=True)
        data = [
            {
                "pk": form.pk,
                "name": form.name,
                "description": form.description,
                "schema": form.schema,
                "approval_required": form.approval_required,
                "approval_flow": form.approval_flow.name if form.approval_flow_id else None,
                "approval_flow_pk": str(form.approval_flow_id or "") or None,
            }
            for form in forms
        ]
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def resubmit(self, request, *args, **kwargs):
        """重新提交被驳回的填报（仅申请人、仅驳回态；按当前数据重新发起流程实例）"""
        instance = self.get_object()
        ok, detail = resubmit_submission(instance, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The submission has been resubmitted"))

    def _create_with_flow(self, serializer):
        """绑定审批流程的提交：事务内建行并发起流程实例，任一失败整体回滚。"""
        from django.db import transaction
        from rest_framework.exceptions import ValidationError

        with transaction.atomic():
            submission = serializer.save(creator=self.request.user, modifier=self.request.user)
            ok, detail = create_flow_instance(submission, self.request.user)
            if not ok:
                raise ValidationError({"detail": detail})
        return ApiResponse(data=self.get_serializer(submission).data, detail=_("Application submitted"))

    def create(self, request, *args, **kwargs):
        """提交：数据校验先行 → 审批门 → 创建。

        审批分三支：
        - 绑定审批流程（form.approval_flow）：进入流程引擎，多级审批，终态回写提交状态；
        - approval_required：敏感操作审批（412 待审批 → 审批人通过 → 申请人携
          X-Approval-Id 重放，服务端校验 creator/指纹/一次性），消费成功才落库；
        - 其余：直接落库。
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        form = serializer.validated_data["form"]

        if form.approval_flow_id:
            return self._create_with_flow(serializer)

        if form.approval_required and not getattr(request.user, "is_superuser", False):
            from system.utils.approval import (
                APPROVAL_HEADER,
                APPROVAL_QUERY_PARAM,
                consume_approval,
                create_approval,
                find_active_pending,
                get_request_params,
                pending_response,
            )

            token = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
            if token:
                response = consume_approval(request, token)
                if response is not None:
                    return response
            else:
                approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
                if approval is None:
                    approval = create_approval(self, request)
                return pending_response(approval)

        self.perform_create(serializer)
        return ApiResponse(data=serializer.data)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return queryset
        # creator 隔离：普通用户只见本人提交
        return queryset.filter(creator=user)

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def _creator_guard(self, instance, request):
        if instance and not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
            return ApiResponse(code=1003, detail=_EDIT_DENY)
        # 审批中的提交不可改动：流程实例按提交快照推进，改动会造成两处数据不一致
        if instance and instance.status == DynamicFormSubmission.Status.PENDING:
            return ApiResponse(code=1003, detail=_PENDING_DENY)
        return None

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        guarded = self._creator_guard(instance, request)
        if guarded:
            return guarded
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        guarded = self._creator_guard(instance, request)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)
