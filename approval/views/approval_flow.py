#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期：

- ApprovalFlowViewSet：流程定义 CRUD（节点列表嵌套写入）；
- ApprovalInstanceViewSet：流程实例（我的申请/待办/已办）+ 发起 / 通过 / 驳回 /
  撤回 / 加签 / 转交（含批量）/ 批量 / 待办计数 / 统计 / 导出。

取值域：超管全部；普通用户「我发起 ∪ 待我审批 ∪ 我参与过」（visible_instances_for）。
"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.viewsets import GenericViewSet

from approval.models.approval import ApprovalFlow, ApprovalInstance, ApprovalNodeTask
from approval.serializers.approval_flow import (
    ApprovalFlowSerializer,
    ApprovalInstanceExportSerializer,
    ApprovalInstanceSerializer,
)
from approval.utils.approval_flow import (
    create_instance,
    visible_instances_for,
)
from approval.utils.approval_mfa import ensure_approval_action_confirmed
from approval.views.approval_instance_actions import ApprovalInstanceActionMixin
from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseModelSet,
    BaseViewSet,
    DetailAction,
    ImpactPreviewAction,
    ListAction,
    RelationCountMixin,
    SearchColumnsAction,
    SearchFieldsAction,
)
from common.core.modelset.import_export.export_actions import OnlyExportDataAction
from common.core.permission import user_has_permission
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.utils.tags import TagChoiceFilter, TagFilterBackend, TagFilterMixin, TaggedPrefetchMixin

#: 「全部在途」管理视角的权限点 path（无独立路由的功能授权，登记于 loadjson/menu.json）
ONGOING_PERMISSION_PATH = "api/system/approval-instances/ongoing$"


class ApprovalFlowFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    code = filters.CharFilter(field_name="code", lookup_expr="icontains")

    class Meta:
        model = ApprovalFlow
        fields = ["name", "code", "is_active", "created_time"]


class ApprovalFlowViewSet(RelationCountMixin, BaseModelSet, ImpactPreviewAction):
    """审批流程定义"""

    queryset = ApprovalFlow.objects.all()
    serializer_class = ApprovalFlowSerializer
    filterset_class = ApprovalFlowFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "name", "updated_time"]
    select_related_fields = ("creator",)
    prefetch_related_fields = ("nodes",)

    def perform_destroy(self, instance):
        """有历史实例的流程禁止删除（实例对流程是 PROTECT，直删会 500，这里给可读错误）。"""
        if instance.instances.exists():
            raise ValidationError({"detail": _("A flow with applications cannot be deleted")})
        return super().perform_destroy(instance)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=["pks"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除：静默排除有历史实例的流程，不因单条受保护而整批失败。

        ⚠️ 覆写基类 `BatchDestroyAction.batch_destroy` 必须保留 `@action`
        装饰器（DRF 靠其 `.mapping` 注册路由），否则端点丢失、请求 405。
        """
        self.queryset = self.queryset.filter(instances__isnull=True)
        return super().batch_destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="versions")
    def versions(self, request, *args, **kwargs):
        """流程定义版本列表（快照审计追溯）。"""
        flow = self.get_object()
        rows = flow.versions.order_by("-version").values("version", "remark", "created_time")
        return ApiResponse(data=list(rows))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "version": build_basic_type(OpenApiTypes.INT),
                    "remark": build_basic_type(OpenApiTypes.STR),
                }
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="rollback")
    def rollback(self, request, *args, **kwargs):
        """回滚到历史版本：快照写入活定义并落新版本；有 PENDING 实例时拒绝。"""
        ensure_approval_action_confirmed(request, "rollback")
        version = request.data.get("version")
        try:
            version = int(version)
        except (TypeError, ValueError):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        flow = self.get_object()
        ok, detail = self.get_serializer().rollback_to_version(flow, version, remark=request.data.get("remark") or "")
        if not ok:
            return ApiResponse(code=1004, detail=detail)
        return ApiResponse(detail=detail)


class ApprovalInstanceFilter(TagFilterMixin, BaseFilterSet):
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")
    tag = TagChoiceFilter()
    flow_name = filters.CharFilter(field_name="flow_name", lookup_expr="icontains")

    class Meta:
        model = ApprovalInstance
        fields = ["title", "flow_name", "status", "flow", "creator", "created_time", "tag"]


class ApprovalInstanceScopeFilter(BaseFilterBackend):
    """页签取值域：scope=pending（待我审批）/ mine（我的申请）/ done（已办）/ ongoing（在途管理）。

    缺省 = 可见域（我发起 ∪ 待我审批 ∪ 我参与过）；超管不设限。
    与 pending_count_for 同口径：本人发起的申请不计入待办（在「我的申请」处理）。

    scope=ongoing 是**管理视角**（全部审批中的申请，供管理员巡看/催办），按权限点
    `ongoing:SystemApprovalInstance` 授权（超管天然具备）——普通用户只能看可见域。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        scope = request.query_params.get("scope")
        if scope == "ongoing":
            if not user_has_permission(user, ONGOING_PERMISSION_PATH, "GET"):
                raise PermissionDenied(_("You do not have permission to view all in-progress applications"))
            from approval.models.approval import ApprovalInstance as _Instance

            return queryset.filter(status=_Instance.Status.PENDING)
        if scope == "pending":
            return (
                queryset.filter(tasks__status=ApprovalNodeTask.Status.PENDING, tasks__assignee=user)
                .exclude(creator=user)
                .distinct()
            )
        if scope == "mine":
            return queryset.filter(creator=user)
        if scope == "done":
            return queryset.filter(tasks__actor=user).distinct()
        return visible_instances_for(user)


class ApprovalInstanceViewSet(
    TaggedPrefetchMixin,
    BaseViewSet,
    # OnlyExportDataAction 继承 ListAction：必须排在 ListAction 之前，否则 MRO 冲突
    OnlyExportDataAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    ApprovalInstanceActionMixin,
    GenericViewSet,
):
    """流程审批中心"""

    queryset = ApprovalInstance.objects.all()
    serializer_class = ApprovalInstanceSerializer
    filterset_class = ApprovalInstanceFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter, ApprovalInstanceScopeFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "finished_at"]
    select_related_fields = ("flow", "creator", "current_node")
    prefetch_related_fields = ("tasks", "tasks__assignee", "tasks__actor")
    # 通用标签：?tag=<标签名> 过滤 + 列表预取（TaggedPrefetchMixin）
    extra_filter_class = [TagFilterBackend]

    def create(self, request, *args, **kwargs):
        """发起申请（按流程 form_schema 填写，落实例并进入首节点）"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        flow = serializer.validated_data["flow"]
        instance, error = create_instance(
            flow=flow,
            applicant=request.user,
            title=serializer.validated_data.get("title"),
            form_data=serializer.validated_data.get("form_data") or {},
            # 抄送人：发起时追加（与节点级默认抄送合并，去重）
            cc_users=request.data.get("cc_users") or [],
        )
        if error:
            raise ValidationError({"detail": error})
        instance.refresh_from_db()
        return ApiResponse(data=self.get_serializer(instance).data, detail=_("Application submitted"))

    def get_serializer_class(self):
        """导出走轻量序列化器（仅表格列，不含 tasks/表单快照）。

        注意不能覆写 ``export_data``——DRF 的路由收集依赖 ``@action`` 装饰器写在方法上，
        覆写会丢掉标记导致 404；这里按 action 名切换序列化器。
        """
        if getattr(self, "action", None) == "export_data":
            return ApprovalInstanceExportSerializer
        return ApprovalInstanceSerializer
