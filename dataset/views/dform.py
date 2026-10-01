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
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImpactPreviewAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from dataset.models.dform import DynamicForm
from dataset.serializers.dform import (
    DynamicFormSerializer,
)


class DynamicFormFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = DynamicForm
        fields = ["is_active", "approval_required"]


class DynamicFormViewSet(BaseModelSet, ImpactPreviewAction):
    """动态表单定义（管理员）。

    模板（is_template）与表单共用一张表：列表默认只出表单（kind=templates 时
    只出模板，供「从模板新建」复用）；详情类动作（编辑/删除/取详情）不做过滤，
    模板因此可被直接维护。
    """

    queryset = DynamicForm.objects.all()
    serializer_class = DynamicFormSerializer
    ordering = ["-created_time"]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = DynamicFormFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "action", None) == "list":
            if self.request.query_params.get("kind") == "templates":
                return queryset.filter(is_template=True)
            return queryset.filter(is_template=False)
        return queryset

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def _needs_rowwise_delete(self):
        """删除表单有逐行副作用（绑定流程的 form_schema 再同步，见 perform_destroy），
        批量删除必须走逐行分支（覆写契约见 docs/architecture/framework-cookbook.md）。"""
        return True

    def perform_destroy(self, instance):
        """删除表单后对绑定流程做 form_schema 再同步（其他绑定表单仍存在时重投影）。"""
        from dataset.utils.dform_flow import resync_flow_after_unbind

        flow_id = instance.approval_flow_id
        super().perform_destroy(instance)
        resync_flow_after_unbind(flow_id)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="schema-history")
    def schema_history(self, request, *args, **kwargs):
        """schema 版本历史（新 → 旧）：每项含 schema 全文，供查看/对比/回滚。

        保留最近 MAX_SCHEMA_HISTORY（20）个版本；版本号单调递增，回滚同样生成新版本。
        """
        form = self.get_object()
        return ApiResponse(
            data={
                "current": form.schema_version or 1,
                "updated_time": form.updated_time.isoformat() if form.updated_time else "",
                "history": list(form.schema_history or []),
            }
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"version": build_basic_type(OpenApiTypes.INT)},
                required=["version"],
                description="要回滚到的历史版本号",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def rollback(self, request, *args, **kwargs):
        """回滚 schema 到指定历史版本：应用其 schema 并生成新版本（历史保留，可再次回滚）。

        写入校验与常规编辑同源（规范化 + 字段/联动校验），避免历史脏数据绕过校验落库。
        """
        form = self.get_object()
        try:
            version = int(request.data.get("version"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("A schema version is required")) from exc
        target = next(
            (item for item in (form.schema_history or []) if int(item.get("version") or 0) == version),
            None,
        )
        if target is None:
            raise ValidationError(_("Schema version {} does not exist").format(version))
        serializer = self.get_serializer(form, data={"schema": target.get("schema") or {}}, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return ApiResponse(
            data=self.get_serializer(form).data,
            detail=_("Rolled back to schema version {}").format(version),
        )
