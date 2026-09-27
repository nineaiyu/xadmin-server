#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表单数据（管理端）视图：按表单浏览 / 筛选 / 导出全部提交。

与「我的填报」（``dataset/views/dform.py::DynamicFormSubmissionViewSet``）的分工：

- 取值域：本视图不做 creator 隔离，走**数据权限编译器**（全局默认
  filter_backends 含 ``common.core.filter.BaseDataPermissionFilter``）——
  超管全量，非超管按角色/部门的授权规则收敛可见行，未配置授权即空集（fail-closed）；
- 能力面：只读（列表 / 详情 / 导出）；提交、修改、重提留在「我的填报」；
- 权限口径：「谁能看」= 页面权限点（本菜单的 list/retrieve/exportData）
  × 数据权限授权（行级），与系统其它管理列表同款两层口径。

数据权限规则的表选项来自 ``PERMISSION_DATA_AUTH_APPS``（已含 dataset app），
通过「数据权限」页配置；授权菜单维度选择「表单数据」或留空（通用）。
"""

from drf_spectacular.utils import extend_schema
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseViewSet,
    DetailAction,
    ListAction,
    OnlyExportDataAction,
    SearchColumnsAction,
    SearchFieldsAction,
)
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.serializers.dform import (
    FormDataDetailSerializer,
    FormDataListSerializer,
    SubmissionExportSerializer,
    export_dynamic_fields,
)
from dataset.utils.dform_history import merged_fields
from system.utils.user_options import search_user_options


class FormDataFilter(BaseFilterSet):
    """表单数据筛选：表单 / 状态 / 提交人（+ 继承的时间范围等通用筛选）。"""

    class Meta:
        model = DynamicFormSubmission
        fields = ["form", "status", "creator"]


class DynamicFormDataViewSet(
    BaseViewSet,
    # OnlyExportDataAction 继承 ListAction：必须排在 ListAction 之前，否则 MRO 冲突
    OnlyExportDataAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """表单记录"""

    # 管理端只读视图：取值域 = 数据权限编译器（超管全量 / 非超管按授权 fail-closed），
    # 无 create / update / destroy / batch-destroy（提交与改动留在「我的填报」）

    queryset = DynamicFormSubmission.objects.select_related("form", "creator")
    serializer_class = FormDataListSerializer
    retrieve_serializer_class = FormDataDetailSerializer
    # 导出专用序列化器（get_serializer_class 按 {action}_serializer_class 自动识别）
    export_data_serializer_class = SubmissionExportSerializer
    ordering = ["-created_time"]
    filterset_class = FormDataFilter
    # filter_backends 不覆写：沿用全局默认（DjangoFilterBackend / OrderingFilter /
    # BaseDataPermissionFilter），数据权限编译器即本视图的取值域

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self, "action", None) == "export_data":
            context["dynamic_fields"] = export_dynamic_fields(self.filter_queryset(self.get_queryset()))
        return context

    @extend_schema(responses=get_default_response_schema())
    @shared_list_action(methods=["get"], detail=False, url_path="form-options")
    def form_options(self, request, *args, **kwargs):
        """表单选项（全部非模板表单，含停用）：管理端「选择表单」数据源。

        返回 schema 供前端渲染动态列；定义类资源不做行级数据权限过滤
        （与 available-forms 同口径），行可见性只作用于提交记录。
        """
        forms = DynamicForm.objects.filter(is_template=False)
        data = []
        for form in forms:
            # schema 的 fields 用「当前 ∪ 历史」合并口径：改版删除的字段以历史标注出列，
            # 管理端列表仍能看到旧提交里的值（与导出口径同源，见 ADR-070）
            schema = dict(form.schema or {})
            schema["fields"] = merged_fields(form)
            data.append(
                {
                    "pk": form.pk,
                    "name": form.name,
                    "description": form.description,
                    "is_active": form.is_active,
                    "schema": schema,
                    "schema_version": form.schema_version or 1,
                }
            )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @shared_list_action(methods=["get"], detail=False, url_path="user-options")
    def user_options(self, request, *args, **kwargs):
        """选人控件数据源（关键字搜索 / 按主键回显，≤20 条）：列表内选人字段回显。

        与「我的填报」的 user-options 同源（common 轻量数据源），供管理端列表把
        选人字段的 pk 展示为用户名；权限与对应 list 权限同口径。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)
