#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏、共享模板与云端剪贴板视图。"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from common.core.filter import BaseFilterSet
from common.core.modelset import ImpactPreviewAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from dataset.models.screen import Screen, ScreenClipboard, ScreenTemplate
from dataset.serializers.screen import (
    ScreenClipboardSerializer,
    ScreenCommandSerializer,
    ScreenSerializer,
    ScreenTemplateSerializer,
    ScreenTemplateVersionSerializer,
    ScreenVersionSerializer,
)
from dataset.services.screen import (
    screen_version_keep,
    snapshot_screen_version,
    snapshot_template_version,
    version_keep,
)
from dataset.views.analysis_common import BaseAnalysisViewSet, _creator_guard
from dataset.ws_screen import (
    apply_screen_command,
    broadcast_screen_command,
    broadcast_screen_data_trigger,
    load_screen_state,
)


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


class ScreenViewSet(BaseAnalysisViewSet, ImpactPreviewAction):
    """大屏

    含远程控制：管理端下发指令，展示端经 ``ws/screen/<pk>`` 接收。
    """

    queryset = Screen.objects.all()
    serializer_class = ScreenSerializer
    filterset_class = ScreenFilter

    def perform_create(self, serializer: Any) -> None:
        super().perform_create(serializer)
        snapshot_screen_version(serializer.instance, actor=self.request.user)

    def perform_update(self, serializer: Any) -> None:
        before = (
            serializer.instance.layout or [],
            serializer.instance.layout_mode,
            serializer.instance.free_layout or {},
        )
        super().perform_update(serializer)
        after = (
            serializer.instance.layout or [],
            serializer.instance.layout_mode,
            serializer.instance.free_layout or {},
        )
        # 仅布局载荷或模式实质变更才记版本：改名 / 可见性 / 主题 / 轮播清单不挤掉布局历史
        if before != after:
            snapshot_screen_version(serializer.instance, actor=self.request.user)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="versions")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def versions(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """大屏布局版本历史

        只读，最新在前；历史属于作者工作台，与回滚同一归属口径
        """
        screen = self.get_object()
        guarded = _creator_guard(request, screen)
        if guarded:
            return guarded
        rows = screen.versions.all()[: screen_version_keep()]
        return ApiResponse(data=ScreenVersionSerializer(rows, many=True).data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="rollback")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rollback(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """回滚大屏布局版本

        恢复两份画布载荷与模式，并再记一版（回滚可逆）
        """
        screen = self.get_object()
        guarded = _creator_guard(request, screen)
        if guarded:
            return guarded
        try:
            version = int(request.data.get("version"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("A layout version is required")) from exc
        with transaction.atomic():
            screen = Screen.objects.select_for_update().get(pk=screen.pk)
            target = screen.versions.filter(version=version).first()
            if target is None:
                return ApiResponse(code=1001, detail=_("Screen version not found"))
            # 快照取自已验证的历史版本，直接还原；两份载荷必须同时回写（模式不互转坐标）
            screen.layout = target.layout or []
            screen.layout_mode = target.layout_mode
            screen.free_layout = target.free_layout or {}
            screen.modifier = request.user
            screen.save(update_fields=["layout", "layout_mode", "free_layout", "modifier", "updated_time"])
            snapshot_screen_version(screen, actor=request.user)
        return ApiResponse(
            data=ScreenSerializer(screen, context=self.get_serializer_context()).data,
            detail=_("Screen layout rolled back"),
        )

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
        # 仅布局载荷变更才记版本：重命名/分组/可见性调整不应挤掉真正的布局历史
        if "free_layout" in self.request.data:
            snapshot_template_version(serializer.instance, actor=self.request.user)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="versions")  # type: ignore[untyped-decorator]
    def versions(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """模板历史版本（只读，最新在前）。"""
        template = self.get_object()
        # 与 rollback 同一归属口径（版本与回滚同一归属）：docstring 即「版本与回滚都只对
        # 创建者开放」，只读端点同样不能越权读他人模板历史
        guarded = _creator_guard(request, template)
        if guarded:
            return guarded
        rows = template.versions.all()[: version_keep()]
        return ApiResponse(data=ScreenTemplateVersionSerializer(rows, many=True).data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="rollback")  # type: ignore[untyped-decorator]
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
