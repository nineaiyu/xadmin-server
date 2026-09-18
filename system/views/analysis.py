#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏与报表视图。

- Screen（大屏模板）：CRUD，无自定义动作；
- Report（定时报表）：CRUD + `run` 立即运行动作（预创建 ExportRecord 并按
  pk==task_id 契约派发，与周期任务同一条管线）。

定义类资源不做行级数据权限过滤（可见性语义 = 创建者/共享，与 Dataset 同款处理）；
非创建者只读。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.analysis_tasks import schedule_report_run
from system.models.dataset import Report, Screen
from system.serializers.analysis import ReportSerializer, ScreenCommandSerializer, ScreenSerializer
from system.utils.user_options import search_user_options
from system.ws_screen import apply_screen_command, broadcast_screen_command, load_screen_state

_EDIT_DENY = "Only the creator can modify it"


def _visible_queryset(viewset):
    """两档可见性：personal 仅创建者；shared 全员（superuser 绕过）。"""
    queryset = viewset.queryset
    user = viewset.request.user
    if getattr(user, "is_superuser", False):
        return queryset
    return queryset.filter(Q(visibility="shared") | Q(creator=user))


def _creator_guard(request, instance):
    if instance and not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
        return ApiResponse(code=1003, detail=_EDIT_DENY)
    return None


class BaseAnalysisViewSet(BaseModelSet):
    """公共：可见性过滤 + 创建者写保护 + 定义类资源豁免行级数据权限。"""

    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def get_queryset(self):
        return _visible_queryset(self)

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        guarded = _creator_guard(request, serializer.instance)
        if guarded:
            return guarded
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        guarded = _creator_guard(request, instance)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)


class ScreenFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Screen
        fields = ["visibility"]


class ReportFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Report
        fields = ["dataset", "frequency", "is_active"]


class ScreenViewSet(BaseAnalysisViewSet):
    """大屏模板（含远程控制：管理端下发指令，展示端经 ws/screen/<pk> 接收）"""

    queryset = Screen.objects.all()
    serializer_class = ScreenSerializer
    filterset_class = ScreenFilter

    @extend_schema(request=ScreenCommandSerializer, responses=get_default_response_schema())
    @action(methods=["get", "post"], detail=True, url_path="command")
    def command(self, request, *args, **kwargs):
        """GET 当前控制态（+仪表盘清单）；POST 下发切换/翻页/刷新/恢复轮播。

        取值域沿用可见性过滤（个人大屏仅创建者可控制）；GET+POST 共享权限码
        `command:DataScreen`（登记见 permission_sync SHARED_METHOD_PATHS）。
        """
        screen = self.get_object()
        if request.method == "GET":
            return ApiResponse(
                data={
                    "state": load_screen_state(screen.pk),
                    "dashboards": [str(item) for item in (screen.dashboards or [])],
                }
            )
        serializer = ScreenCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            frame = apply_screen_command(
                screen,
                serializer.validated_data["command"],
                dashboard_pk=serializer.validated_data.get("dashboard_pk") or "",
                index=serializer.validated_data.get("index"),
            )
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        broadcast_screen_command(screen.pk, frame)
        return ApiResponse(data={"state": frame}, detail=_("Command sent"))


class ReportViewSet(BaseAnalysisViewSet):
    """定时报表"""

    queryset = Report.objects.all()
    serializer_class = ReportSerializer
    filterset_class = ReportFilter

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")
    def user_options(self, request, *args, **kwargs):
        """IM 收件人候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        口径与选人控件同源（system/utils/user_options.py）；权限与该视图 list
        权限同口径（common/core/permission.py 的 user-options 特例）。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="run")
    def run(self, request, *args, **kwargs):
        """立即运行一次报表（预创建 ExportRecord 并按契约派发）。"""
        report = self.get_object()
        guarded = _creator_guard(request, report)
        if guarded:
            return guarded
        task_id = schedule_report_run(str(report.pk))
        return ApiResponse(data={"task_id": task_id})
