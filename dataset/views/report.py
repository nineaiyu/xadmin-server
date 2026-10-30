#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表视图。"""

from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from common.core.filter import BaseFilterSet
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from dataset.analysis_tasks import schedule_report_run
from dataset.models.report import Report
from dataset.serializers.report import (
    IM_NOTIFY_CHANNELS,
    REPORT_NOTIFY_CHANNELS,
    ReportSerializer,
    ReportVersionSerializer,
)
from dataset.services.report import snapshot_report_version, version_keep
from dataset.utils.dataset import dataset_visible_to
from dataset.views.analysis_common import BaseAnalysisViewSet, _creator_guard
from identity.utils.user_options import search_user_options


class ReportFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Report
        fields = ["dataset", "frequency", "is_active"]


class ReportViewSet(BaseAnalysisViewSet):
    """定时报表"""

    queryset = Report.objects.all()
    serializer_class = ReportSerializer
    filterset_class = ReportFilter

    def perform_create(self, serializer: Any) -> None:
        with transaction.atomic():
            super().perform_create(serializer)
            snapshot_report_version(serializer.instance, actor=self.request.user)

    def perform_update(self, serializer: Any) -> None:
        base_version = (getattr(serializer, "initial_data", {}) or {}).get("base_version")
        with transaction.atomic():
            # 主表保存与版本快照同事务：任一步失败整体回滚，避免 design 与
            # design_version/版本表漂移
            locked = type(serializer.instance).objects.select_for_update().get(pk=serializer.instance.pk)
            # 乐观锁在行锁后复核：serializer.validate 比对的是锁前实例，两个并发保存
            # 都可能通过；以 locked 版本为准，后提交者得到冲突提示而不是覆盖。
            if base_version is not None and int(base_version) != int(locked.design_version or 1):
                raise ValidationError(
                    {"base_version": _("The content has been modified by another user; reload before saving")}
                )
            before = locked.design or {}
            serializer.instance = locked
            super().perform_update(serializer)
            # 仅设计载荷实质变更才记版本：改名 / 调度 / 收件人调整不挤掉设计历史
            if before != (serializer.instance.design or {}):
                snapshot_report_version(serializer.instance, actor=self.request.user)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="versions")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def versions(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """报表设计版本历史

        只读，最新在前；历史属于作者工作台，与回滚同一归属口径
        """
        report = self.get_object()
        guarded = _creator_guard(request, report)
        if guarded:
            return guarded
        rows = report.versions.all()[: version_keep()]
        return ApiResponse(data=ReportVersionSerializer(rows, many=True).data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="rollback")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rollback(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """回滚报表设计版本

        恢复历史设计并再记一版（回滚可逆）
        """
        report = self.get_object()
        guarded = _creator_guard(request, report)
        if guarded:
            return guarded
        try:
            version = int(request.data.get("version"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("A design version is required")) from exc
        with transaction.atomic():
            report = Report.objects.select_for_update().get(pk=report.pk)
            target = report.versions.filter(version=version).first()
            if target is None:
                return ApiResponse(code=1001, detail=_("Report version not found"))
            report.design = target.design or {}
            report.modifier = request.user
            report.save(update_fields=["design", "modifier", "updated_time"])
            snapshot_report_version(report, actor=request.user)
        return ApiResponse(
            data=ReportSerializer(report, context=self.get_serializer_context()).data,
            detail=_("Report design rolled back"),
        )

    @extend_schema(responses=get_default_response_schema())
    @shared_list_action(methods=["get"], detail=False, url_path="user-options")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def user_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """IM 收件人候选

        按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）

        口径与选人控件同源（identity/utils/user_options.py）；权限与该视图 list
        权限同口径（packages/xadmin-common/common/core/permission.py 的 user-options 特例）。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="choices")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def choices(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """投递渠道枚举

        结构元数据：值集单源在本模块常量，前端不再手抄选项表

        与其它 choices 消费方同口径（``choices_dict`` 在响应顶层）；路径命中
        ``^/api/.*choices$`` 结构元数据白名单，无需独立权限点。``im_notify_channels``
        显式下发 IM 子集，前端不再按「非 email」隐式推导。
        """
        return ApiResponse(
            choices_dict={
                "notify_channels": list(REPORT_NOTIFY_CHANNELS),
                "im_notify_channels": list(IM_NOTIFY_CHANNELS),
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="run")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def run(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """立即运行一次报表

        预创建 ExportRecord 并按契约派发
        """
        report = self.get_object()
        guarded = _creator_guard(request, report)
        if guarded:
            return guarded
        # 运行前再校验一次可见性（保存时的校验不覆盖「数据集后来被改为 personal」）：
        # 执行以 report.creator 的身份查数，闭包按执行者判定（任务内还有一道复核）
        if not dataset_visible_to(request.user, report.dataset_id):
            return ApiResponse(
                code=1001,
                detail=_("The dataset is not visible to you (personal dataset of another user)"),
            )
        task_id = schedule_report_run(str(report.pk))
        return ApiResponse(data={"task_id": task_id})
