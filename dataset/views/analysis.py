#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏与报表视图。

- Screen（大屏模板）：CRUD，无自定义动作；
- Report（定时报表）：CRUD + `run` 立即运行动作（预创建 ExportRecord 并按
  pk==task_id 契约派发，与周期任务同一条管线）。

定义类资源不做行级数据权限过滤（可见性语义 = 创建者/共享，与 Dataset 同款处理）；
非创建者只读。
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, FieldError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ImpactPreviewAction
from common.core.permission_meta import shared_list_action
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from dataset.analysis_tasks import schedule_report_run
from dataset.models.dataset import (
    Report,
    Screen,
    ScreenClipboard,
    ScreenTemplate,
    ScreenTemplateVersion,
)
from dataset.serializers.analysis import (
    IM_NOTIFY_CHANNELS,
    REPORT_NOTIFY_CHANNELS,
    ReportSerializer,
    ScreenClipboardSerializer,
    ScreenCommandSerializer,
    ScreenSerializer,
    ScreenTemplateSerializer,
    ScreenTemplateVersionSerializer,
)
from dataset.utils.screen_layout import SCREEN_TEMPLATE_VERSION_KEEP
from dataset.ws_screen import (
    apply_screen_command,
    broadcast_screen_command,
    broadcast_screen_data_trigger,
    load_screen_state,
)
from identity.utils.user_options import search_user_options

_EDIT_DENY = "Only the creator can modify it"


def _visible_queryset(viewset: Any) -> Any:
    """两档可见性：personal 仅创建者；shared 全员（superuser 绕过）。

    ``visibility`` 是数据集 / 看板 / 大屏的共享维度；报表等无该字段的资源配置
    不做可见性收敛（定义类资源本就豁免行级数据权限，写保护由 ``_creator_guard``
    承担）——不可无条件按 ``visibility`` 过滤，否则非超管访问该类资源直接 500。
    """
    queryset = viewset.queryset
    user = viewset.request.user
    if getattr(user, "is_superuser", False):
        return queryset
    if not _supports_visibility(queryset.model):
        return queryset
    return queryset.filter(Q(visibility="shared") | Q(creator=user))


def _supports_visibility(model: Any) -> bool:
    """模型是否具备可见性维度（`visibility` 字段）。

    ``_meta.get_field`` 对缺失字段抛 ``FieldDoesNotExist``（不是 ``FieldError``
    的子类），两者都要纳入兜底。
    """
    try:
        model._meta.get_field("visibility")
    except (FieldError, FieldDoesNotExist):
        return False
    return True


def _creator_guard(request: Any, instance: Any) -> Any:
    if instance and not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
        return ApiResponse(code=1003, detail=_EDIT_DENY)
    return None


class BaseAnalysisViewSet(BaseModelSet):
    """分析域视图基类

    可见性过滤 + 创建者写保护 + 定义类资源豁免行级数据权限
    """

    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def get_queryset(self) -> Any:
        return _visible_queryset(self)

    def perform_create(self, serializer: Any) -> None:
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        guarded = _creator_guard(request, serializer.instance)
        if guarded:
            return guarded
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        instance = self.get_object()
        guarded = _creator_guard(request, instance)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)


def _version_keep() -> int:
    """版本保留数：允许 settings 覆盖（便于按环境/测试调小），缺省取领域常量。"""
    return int(getattr(settings, "SCREEN_TEMPLATE_VERSION_KEEP", SCREEN_TEMPLATE_VERSION_KEEP))


def snapshot_template_version(template: Any, actor: Any = None) -> Any:
    """给模板记一版快照，并把超出保留数的旧版本裁掉。

    只在有窗格时记（空载荷不是有效模板）；版本号沿用「当前最大 + 1」，删除最旧的若干条。
    """
    if not (template.free_layout or {}).get("panes"):
        return None
    latest = template.versions.first()
    version = (latest.version if latest else 0) + 1
    snapshot = ScreenTemplateVersion.objects.create(
        template=template,
        version=version,
        name=template.name,
        group=template.group,
        free_layout=template.free_layout,
        creator=actor,
        modifier=actor,
    )
    stale = list(template.versions.values_list("pk", flat=True)[_version_keep() :])
    if stale:
        ScreenTemplateVersion.objects.filter(pk__in=stale).delete()
    return snapshot


class ScreenFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Screen
        fields = ["visibility"]


class ScreenTemplateFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = ScreenTemplate
        fields = ["visibility"]


class ReportFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Report
        fields = ["dataset", "frequency", "is_active"]


class ScreenViewSet(BaseAnalysisViewSet, ImpactPreviewAction):
    """大屏

    含远程控制：管理端下发指令，展示端经 ``ws/screen/<pk>`` 接收。
    """

    queryset = Screen.objects.all()
    serializer_class = ScreenSerializer
    filterset_class = ScreenFilter

    @extend_schema(request=ScreenCommandSerializer, responses=get_default_response_schema())
    @action(methods=["get", "post"], detail=True, url_path="command")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def command(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """大屏远程控制

        +仪表盘清单；POST 下发切换/翻页/刷新/恢复轮播

        控制权口径（与 report.run 的创建者守卫刻意不同，评估结论=维持现状并明确）：
        - personal 大屏：取值域仅创建者（或超管）→ 即仅创建者可控制；
        - shared 大屏：协作语义——持有 `command:DataScreen`（管理员级）权限的用户
          均可控制，控制权由可见性过滤划定（shared 的存在意义即团队投屏与切换），
          不叠加 owner 守卫；若需"仅创建者可控制"，请使用 personal 可见性。
        GET+POST 共享权限码 `command:DataScreen`（登记见 permission_sync SHARED_METHOD_PATHS）。
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
        if serializer.validated_data["command"] == "refresh":
            # refresh 立即触发一轮服务端聚合推送（switch/page/auto 只动浏览位置，不重拉数据）；
            # 触发事件无载荷，各在线展示连接以自身权限聚合后自推，见 ws_screen 模块 docstring
            broadcast_screen_data_trigger(screen.pk)
        return ApiResponse(data={"state": frame}, detail=_("Command sent"))


class ScreenTemplateViewSet(BaseAnalysisViewSet):
    """大屏模板

    与 ScreenViewSet 的差异：无远程控制 action、不参与影响预览（删模板不会让谁渲染不出来）；
    额外提供**版本历史**（保存即记一版，保留最近 N 版）与**回滚**（回滚本身也记一版，可再回滚）。

    版本与回滚都只对**创建者**开放（复用行级归属守卫的 1003 语义）：模板是团队共享资产，
    但历史属于作者自己的工作台。
    """

    queryset = ScreenTemplate.objects.all()
    serializer_class = ScreenTemplateSerializer
    filterset_class = ScreenTemplateFilter

    def perform_create(self, serializer: Any) -> None:
        super().perform_create(serializer)
        snapshot_template_version(serializer.instance, actor=self.request.user)

    def perform_update(self, serializer: Any) -> None:
        super().perform_update(serializer)
        snapshot_template_version(serializer.instance, actor=self.request.user)

    @extend_schema(responses=get_default_response_schema())
    @shared_list_action(methods=["get"], detail=True, url_path="versions")  # type: ignore[untyped-decorator]
    def versions(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """模板历史版本（只读，最新在前）。"""
        template = self.get_object()
        rows = template.versions.all()[: _version_keep()]
        return ApiResponse(data=ScreenTemplateVersionSerializer(rows, many=True).data)

    @extend_schema(responses=get_default_response_schema())
    @shared_list_action(methods=["post"], detail=True, url_path="rollback")  # type: ignore[untyped-decorator]
    def rollback(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """回滚到指定版本

        恢复名称 / 分组 / 载荷，并**再记一版**（回滚可逆）
        """
        template = self.get_object()
        guarded = _creator_guard(request, template)
        if guarded:
            return guarded
        version = request.data.get("version")
        target = template.versions.filter(version=version).first()
        if target is None:
            return ApiResponse(code=1001, detail=_("Template version not found"))
        template.name = target.name
        template.group = target.group
        template.free_layout = target.free_layout
        template.modifier = request.user
        template.save(update_fields=["name", "group", "free_layout", "modifier", "updated_time"])
        snapshot_template_version(template, actor=request.user)
        return ApiResponse(
            data=ScreenTemplateSerializer(template, context=self.get_serializer_context()).data,
            detail=_("Template rolled back"),
        )


class ScreenClipboardViewSet(BaseAnalysisViewSet):
    """大屏云端剪贴板

    **按用户单槽**，让「复制 → 换设备 → 粘贴」成立。

    - 列表只返回本人槽位（0 或 1 条）；创建即更新本人槽位（upsert，避免前端判空两条路径）；
    - 删除即清空（按 pk 删本人槽位）；
    - 权限复用分析域（登录 + 端点权限点），载荷校验与模板/自由画布同一份白名单。
    """

    serializer_class = ScreenClipboardSerializer

    def get_queryset(self) -> Any:
        user = self.request.user
        if not user or not user.is_authenticated:
            return ScreenClipboard.objects.none()
        return ScreenClipboard.objects.filter(user=user)

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """保存云端剪贴板

        本人已有槽位则就地更新，否则新建（前端不必区分首次 / 再次复制）
        """
        serializer = self.get_serializer(self.get_queryset().first(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user, creator=request.user, modifier=request.user)
        return ApiResponse(data=serializer.data)

    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """清空云端剪贴板"""
        return super().destroy(request, *args, **kwargs)


class ReportViewSet(BaseAnalysisViewSet):
    """定时报表"""

    queryset = Report.objects.all()
    serializer_class = ReportSerializer
    filterset_class = ReportFilter

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
        task_id = schedule_report_run(str(report.pk))
        return ApiResponse(data={"task_id": task_id})
