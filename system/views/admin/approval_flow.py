#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期（ADR-012）：

- ApprovalFlowViewSet：流程定义 CRUD（节点列表嵌套写入）；
- ApprovalInstanceViewSet：流程实例（我的申请/待办/已办）+ 发起 / 通过 / 驳回 /
  撤回 / 加签 / 批量 / 待办计数 / 统计。

取值域：超管全部；普通用户「我发起 ∪ 待我审批 ∪ 我参与过」（visible_instances_for）。
"""

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import BaseFilterBackend, OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, BaseViewSet, DetailAction, ListAction, SearchColumnsAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.approval import ApprovalFlow, ApprovalInstance, ApprovalNodeTask
from system.serializers.approval_flow import ApprovalFlowSerializer, ApprovalInstanceSerializer
from system.utils.approval_flow import (
    FLOW_STATS_WINDOW_DAYS,
    add_sign,
    approve_task,
    cancel_instance,
    create_instance,
    instance_stats,
    pending_count_for,
    reject_task,
    visible_instances_for,
)


class ApprovalFlowFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    code = filters.CharFilter(field_name="code", lookup_expr="icontains")

    class Meta:
        model = ApprovalFlow
        fields = ["name", "code", "is_active", "created_time"]


class ApprovalFlowViewSet(BaseModelSet):
    """审批流程定义"""

    queryset = ApprovalFlow.objects.all()
    serializer_class = ApprovalFlowSerializer
    filterset_class = ApprovalFlowFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "name", "updated_time"]
    select_related_fields = ("creator",)
    prefetch_related_fields = ("nodes",)

    def get_queryset(self):
        # node_count 走 annotate 而非逐行 count（列表 N+1）；annotate 会清掉 Meta.ordering，显式补回
        return super().get_queryset().annotate(nodes_count=Count("nodes")).order_by(*ApprovalFlow._meta.ordering)

    def perform_destroy(self, instance):
        """有历史实例的流程禁止删除（实例对流程是 PROTECT，直删会 500，这里给可读错误）。"""
        if instance.instances.exists():
            raise ValidationError({"detail": _("A flow with applications cannot be deleted")})
        return super().perform_destroy(instance)

    def batch_destroy(self, request, *args, **kwargs):
        """批量删除：静默排除有历史实例的流程，不因单条受保护而整批失败。"""
        self.queryset = self.queryset.filter(instances__isnull=True)
        return super().batch_destroy(request, *args, **kwargs)


class ApprovalInstanceFilter(BaseFilterSet):
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")
    flow_name = filters.CharFilter(field_name="flow_name", lookup_expr="icontains")

    class Meta:
        model = ApprovalInstance
        fields = ["title", "flow_name", "status", "flow", "creator", "created_time"]


class ApprovalInstanceScopeFilter(BaseFilterBackend):
    """页签取值域：scope=pending（待我审批）/ mine（我的申请）/ done（已办）。

    缺省 = 可见域（我发起 ∪ 待我审批 ∪ 我参与过）；超管不设限。
    与 pending_count_for 同口径：本人发起的申请不计入待办（在「我的申请」处理）。
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        scope = request.query_params.get("scope")
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
    BaseViewSet,
    ListAction,
    DetailAction,
    SearchColumnsAction,
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
        )
        if error:
            raise ValidationError({"detail": error})
        instance.refresh_from_db()
        return ApiResponse(data=self.get_serializer(instance).data, detail=_("Application submitted"))

    def _resolve_task(self, instance, request):
        """定位要处理的任务：优先请求体 task，缺省取「我的当前待办」（便于前端一键处理）。"""
        task_pk = request.data.get("task")
        queryset = instance.tasks.all()
        if task_pk:
            queryset = queryset.filter(pk=task_pk)
        else:
            queryset = queryset.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            )
        task = queryset.first()
        if task is None:
            raise ValidationError({"detail": _("No pending task available for the current user")})
        return task

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks"],
                description="主键列表 + 审批意见",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-approve")
    def batch_approve(self, request, *args, **kwargs):
        """批量通过（逐个定位当前用户的待办任务，返回成功数与被拒明细）"""
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        comment = (request.data.get("comment") or "").strip()
        succeeded, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        for instance in queryset:
            task = instance.tasks.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            ).first()
            if task is None:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(_("No pending task for you"))})
                continue
            ok, detail = approve_task(task.pk, request.user, comment)
            if ok:
                succeeded += 1
            else:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(detail)})
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Approved {} data").format(succeeded),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "reason": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "reason"],
                description="主键列表 + 驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-reject")
    def batch_reject(self, request, *args, **kwargs):
        """批量驳回（原因必填）"""
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        pks = request.data.get("pks") or []
        if not pks:
            raise ValidationError(_("Please select the data to operate"))
        succeeded, failed = 0, []
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        for instance in queryset:
            task = instance.tasks.filter(
                assignee=request.user, status=ApprovalNodeTask.Status.PENDING, node=instance.current_node
            ).first()
            if task is None:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(_("No pending task for you"))})
                continue
            ok, detail = reject_task(task.pk, request.user, reason)
            if ok:
                succeeded += 1
            else:
                failed.append({"no": str(instance.pk)[:8].upper(), "reason": str(detail)})
        return ApiResponse(
            data={"succeeded": succeeded, "failed": failed},
            detail=_("Operation successful. Rejected {} data").format(succeeded),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="available-flows")
    def available_flows(self, request, *args, **kwargs):
        """可发起流程（启用中）：发起申请弹窗的数据源（无需流程定义管理权限）。

        普通申请人通常没有「流程定义」页权限，因此单独开一个轻量只读入口，
        只回传发起所需的 pk/name/form_schema，不暴露节点审批人配置。
        """
        flows = ApprovalFlow.objects.filter(is_active=True).order_by("-created_time")
        return ApiResponse(
            data=[
                {
                    "pk": str(flow.pk),
                    "name": flow.name,
                    "code": flow.code,
                    "form_schema": flow.form_schema or [],
                }
                for flow in flows
            ]
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="pending-count")
    def pending_count(self, request, *args, **kwargs):
        """待我审批数（轻量接口：供顶栏/页签角标轮询，服务端 10s 短缓存）"""
        return ApiResponse(data={"pending": pending_count_for(request.user)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def stats(self, request, *args, **kwargs):
        """流程审批统计（近 30 天：我提交 / 我通过 / 我驳回 / 我的待办）"""
        return ApiResponse(data=instance_stats(request.user, days=FLOW_STATS_WINDOW_DAYS))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                description="任务主键（缺省取我的当前待办）+ 审批意见",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def approve(self, request, *args, **kwargs):
        """通过（或签任一通过 / 会签全部通过后流转下一节点）"""
        instance = self.get_object()
        task = self._resolve_task(instance, request)
        ok, detail = approve_task(task.pk, request.user, (request.data.get("comment") or "").strip())
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval task has been approved"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "task": build_basic_type(OpenApiTypes.STR),
                    "reason": build_basic_type(OpenApiTypes.STR),
                },
                required=["reason"],
                description="任务主键（缺省取我的当前待办）+ 驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def reject(self, request, *args, **kwargs):
        """驳回（原因必填；驳回即终止申请）"""
        instance = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        task = self._resolve_task(instance, request)
        ok, detail = reject_task(task.pk, request.user, reason)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approval task has been rejected"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def cancel(self, request, *args, **kwargs):
        """撤回申请（仅申请人、仅审批中）"""
        instance = self.get_object()
        ok, detail = cancel_instance(instance, request.user)
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The application has been cancelled"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "usernames": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["usernames"],
                description="加签审批人用户名（逗号分隔）+ 说明",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="add-sign")
    def add_sign_action(self, request, *args, **kwargs):
        """加签：在当前节点追加审批人（当前节点参与人或超管可操作）"""
        instance = self.get_object()
        if not (
            request.user.is_superuser
            or instance.tasks.filter(Q(assignee=request.user) | Q(actor=request.user)).exists()
        ):
            raise PermissionDenied(_("Permission denied"))
        ok, detail = add_sign(
            instance, request.user, request.data.get("usernames"), (request.data.get("comment") or "").strip()
        )
        if not ok:
            return ApiResponse(code=1001, detail=detail)
        return ApiResponse(detail=_("The approver has been added"))
