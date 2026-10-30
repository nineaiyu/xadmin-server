#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏序列化器（自 analysis.py 拆分，行为不变）。

大屏 / 模板 / 模板历史版本 / 云端剪贴板 / 远程控制指令。定义类资源，豁免字段
权限裁剪；窗格引用（仪表盘 / 数据集）按提交者可见域取交集（fail-closed）。
"""

from collections.abc import Collection
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from dataset.models.dataset import (
    Dashboard,
    Dataset,
    Screen,
    ScreenClipboard,
    ScreenTemplate,
    ScreenTemplateVersion,
    ScreenVersion,
)
from dataset.utils.dataset import numeric_columns_of, visible_dataset_queryset
from dataset.utils.screen_layout import (
    SCREEN_MAX_GROUP,
    ScreenLayoutError,
    normalize_screen_clipboard,
    normalize_screen_free_layout,
    normalize_screen_layout,
    normalize_screen_template,
    normalize_screen_theme,
    normalize_screen_theme_color,
)


def request_user(serializer: Any) -> Any:
    """取当前提交用户（无请求上下文时返回 None，按「最小可见域」处理）。"""
    return getattr(serializer.context.get("request"), "user", None)


def visible_dashboard_pks(user: Any, referenced: Any) -> set[Any]:
    """按引用主键在「当前提交用户可见」的仪表盘内取交集（只查主键，不整表加载）。

    可见域与 DashboardViewSet 读侧同口径：超管全量，其余为 shared 或本人创建；
    主键形态非法的引用按「不可见」处理（主键查询遇非法值会抛错，先行拦截）。
    引用不可见的个人仪表盘会令展示端整格静默空，与未知主键同语义拒绝。
    """
    valid = set()
    for pk in referenced:
        try:
            Dashboard._meta.pk.to_python(str(pk))
        except ValidationError:
            continue
        valid.add(str(pk))
    queryset = Dashboard.objects.all()
    if user is None or not getattr(user, "is_superuser", False):
        queryset = queryset.filter(Q(visibility=Dashboard.Visibility.SHARED) | Q(creator=user))
    return {str(pk) for pk in queryset.filter(pk__in=valid).values_list("pk", flat=True)}


def layout_references(user: Any, panes: Any) -> tuple[set[Any], Collection[object], dict[str, Any]]:
    """收集窗格引用并装载校验白名单（大屏栅格 / 自由载荷 / 模板载荷共用）。

    只查窗格引用到的仪表盘主键与指标卡数据集（都按当前用户可见域取交集，
    不整表加载；存在性与数值列校验都不需要整表）；引用串先过主键形态校验，
    非法形态按「不存在」处理（主键查询遇非法值会抛错）。

    返回 ``(known_dashboards, known_datasets, datasets)``：``datasets`` 键为
    ``str(pk)``，供 sum/avg 数值列校验复用。
    """
    items = panes if isinstance(panes, list) else []
    referenced_dashboards = set()
    referenced_datasets = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        pane_type = str(item.get("type") or "dashboard")
        if pane_type == "dashboard":
            dashboard_pk = str(item.get("dashboard") or "").strip()
            if dashboard_pk:
                referenced_dashboards.add(dashboard_pk)
        elif pane_type == "metric":
            dataset_pk = str(item.get("dataset") or "").strip()
            if dataset_pk:
                referenced_datasets.add(dataset_pk)

    known_dashboards = visible_dashboard_pks(user, referenced_dashboards)
    valid_pks = []
    for dataset_pk in referenced_datasets:
        try:
            Dataset._meta.pk.to_python(dataset_pk)
        except ValidationError:
            continue
        valid_pks.append(dataset_pk)
    # 数据集按当前用户的可见域取交集（与仪表盘同口径）：存在但不可见的 personal
    # 数据集一律按「未知引用」拒绝，否则低权用户能把他人数据集挂进自己的大屏。
    datasets = {
        str(item.pk): item
        for item in visible_dataset_queryset(user).filter(pk__in=valid_pks).only("pk", "bound_model", "columns")
    }
    # 容器统一为「成员对象集合」视角：keys 视图与占位单元素集共用一个类型面
    known_datasets: Collection[object] = datasets.keys()
    if referenced_datasets and not datasets and Dataset.objects.exists():
        # 引用的数据集全部不存在：known 集为空会令窗格存在性校验被跳过，
        # 放入窗格不可能命中的占位值，保持「引用必须存在」的既有拒绝语义
        known_datasets = {object()}
    return known_dashboards, known_datasets, datasets


def check_metric_value_fields(panes: Any, datasets: dict[str, Any]) -> None:
    """指标卡窗格的 sum/avg 取值列必须落在该数据集的数值列白名单内。

    与报表组件同口径的 fail-closed：源头挡住执行期必然 400 的组合。
    """
    for pane in panes:
        if pane["type"] != "metric" or pane.get("metric") in (None, "count"):
            continue
        dataset = datasets.get(pane["dataset"])
        if dataset is None:
            continue
        if pane.get("value_field") not in numeric_columns_of(dataset):
            raise serializers.ValidationError(_("Value field is not numeric in pane: {}").format(pane["pk"]))


class ScreenSerializer(BaseModelSerializer):
    """大屏模板。可见性同 Dataset（personal/shared）；创建者与超管可改。"""

    ignore_field_permission = True
    # 非创建者修改/删除被写守卫拒绝（1003）：下发 is_owner 供行内按钮显隐
    row_owner_guard = True
    # 乐观锁基线（write_only）：客户端回传所见行的布局版本，保存时与当前版本比对
    base_version = serializers.IntegerField(write_only=True, required=False, min_value=0)

    # 列表形态派生：canvas-grid / canvas-free / carousel（与投屏端 active_screen_panes 同源）
    render_mode = serializers.SerializerMethodField(label=_("Render mode"))

    def get_render_mode(self, obj: Any) -> str:
        from dataset.utils.screen_layout import SCREEN_LAYOUT_MODE_FREE, active_screen_panes

        if not active_screen_panes(obj):
            return "carousel"
        return "canvas-free" if obj.layout_mode == SCREEN_LAYOUT_MODE_FREE else "canvas-grid"

    class Meta:
        model = Screen
        fields = [
            "pk",
            "name",
            "dashboards",
            "layout",
            "layout_mode",
            "free_layout",
            "layout_version",
            "base_version",
            "render_mode",
            "interval",
            "refresh",
            "visibility",
            "theme",
            "theme_color",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "layout_version", "created_time", "updated_time"]
        # RePlusPage 列表列（主题色不进列表：色彩单元格无可读信息）
        table_fields = [
            "name",
            "render_mode",
            "dashboards",
            "interval",
            "refresh",
            "visibility",
            "theme",
            "updated_time",
        ]

    def _visible_dashboard_pks(self, referenced: Any) -> set[Any]:
        """当前用户可见的仪表盘主键交集（实现见模块级 ``visible_dashboard_pks``）。"""
        return visible_dashboard_pks(request_user(self), referenced)

    def validate_dashboards(self, value: Any) -> Any:
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid screen dashboards"))
        # 存在性 + 可见性一并校验：轮播清单里引用不可见仪表盘同样整页渲染不出
        visible = self._visible_dashboard_pks(value)
        for pk in value:
            if str(pk) not in visible:
                raise serializers.ValidationError(_("Unknown dashboard in layout"))
        return value

    def _layout_references(self, panes: Any) -> tuple[set[Any], Collection[object], dict[str, Any]]:
        """收集窗格引用并装载校验白名单（实现见模块级 ``layout_references``）。"""
        return layout_references(request_user(self), panes)

    def _check_metric_value_fields(self, panes: Any, datasets: dict[str, Any]) -> None:
        """指标卡 sum/avg 取值列校验（实现见模块级 ``check_metric_value_fields``）。"""
        check_metric_value_fields(panes, datasets)

    def validate_layout(self, value: Any) -> Any:
        """栅格窗格载荷：规范化 + 越界/重叠/未知仪表盘/数据集校验。

        单一事实源见 dataset.utils.screen_layout；仪表盘白名单按当前提交用户的可见域
        过滤（shared 或本人创建）——存在但不可见的引用同样拒绝，避免低权用户挂入
        自己不可见的个人仪表盘。
        """
        known_dashboards, known_datasets, datasets = self._layout_references(value)
        try:
            layout = normalize_screen_layout(value, known_dashboards, known_datasets)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        self._check_metric_value_fields(layout, datasets)
        return layout

    def validate_free_layout(self, value: Any) -> Any:
        """自由画布载荷：像素坐标 + 基准设计画幅（允许重叠，层级 = 数组顺序）。

        类型字段与栅格版共用同一套规则与常量（``_normalize_pane_body``），
        差异只在坐标段与不判重叠；空载荷归一化为 ``{}``。
        """
        panes = value.get("panes") if isinstance(value, dict) else None
        known_dashboards, known_datasets, datasets = self._layout_references(panes)
        try:
            free_layout = normalize_screen_free_layout(value, known_dashboards, known_datasets)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        self._check_metric_value_fields(free_layout.get("panes") or [], datasets)
        return free_layout

    def validate(self, attrs: Any) -> Any:
        """校验「写入载荷」与（合并后的）layout_mode 的对应关系。

        投屏形态判定是「自由载荷非空优先、其次栅格载荷、皆空轮播」：写进去却与模式
        不匹配的载荷永远不会被渲染（静默回退到另一形态），必然是一次误操作，直接拒绝。
        未写入的另一模式载荷原样保留——模式来回切换不丢布局。
        """
        base_version = attrs.pop("base_version", None)
        if (
            base_version is not None
            and self.instance is not None
            and int(base_version) != int(self.instance.layout_version or 1)
        ):
            raise serializers.ValidationError(
                {"base_version": _("The content has been modified by another user; reload before saving")}
            )
        mode = attrs.get("layout_mode") or getattr(self.instance, "layout_mode", Screen.LayoutMode.GRID)
        if mode == Screen.LayoutMode.FREE:
            if attrs.get("layout"):
                raise serializers.ValidationError(_("Grid layout requires grid mode"))
        elif (attrs.get("free_layout") or {}).get("panes"):
            raise serializers.ValidationError(_("Free layout requires free mode"))
        return attrs

    def validate_interval(self, value: Any) -> Any:
        if not (5 <= int(value) <= 3600):
            raise serializers.ValidationError(_("Interval must be between 5 and 3600 seconds"))
        return value

    def validate_refresh(self, value: Any) -> Any:
        if not (10 <= int(value) <= 3600):
            raise serializers.ValidationError(_("Refresh must be between 10 and 3600 seconds"))
        return value

    def validate_theme(self, value: Any) -> str:
        """投屏主题：深色 / 浅色 / 跟随站点（空值回落深色，非法值 400）。"""
        try:
            return normalize_screen_theme(value)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_theme_color(self, value: Any) -> str:
        """大屏主题色：hex（空 = 跟随站点主色）；``#rgb`` 统一展开为小写 ``#rrggbb``。"""
        try:
            return normalize_screen_theme_color(value)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ScreenTemplateSerializer(BaseModelSerializer):
    """大屏模板（团队共享）：载荷与自由画布同形，复用同一份归一化与校验。

    引用可见性口径与保存大屏一致（引用不可见即拒绝）——模板是「将来要落到大屏上的
    布局」，放行不可见引用只会把 400 推迟到应用模板那一刻。
    """

    ignore_field_permission = True
    # 非创建者修改/删除被写守卫拒绝（1003）：下发 is_owner 供行内按钮显隐
    row_owner_guard = True
    base_version = serializers.IntegerField(write_only=True, required=False, min_value=0)

    class Meta:
        model = ScreenTemplate
        fields = [
            "pk",
            "base_version",
            "name",
            "group",
            "free_layout",
            "visibility",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "created_time", "updated_time"]
        # RePlusPage 列表列
        table_fields = ["name", "group", "visibility", "updated_time"]

    def validate(self, attrs: Any) -> Any:
        """模板乐观锁：客户端携带 base_version 时与当前最新版本比对。"""
        base_version = attrs.pop("base_version", None)
        if base_version is not None and self.instance is not None:
            latest = self.instance.versions.first()
            current = latest.version if latest else 0
            if int(base_version) != int(current):
                raise serializers.ValidationError(
                    {"base_version": _("The content has been modified by another user; reload before saving")}
                )
        return attrs

    def validate_group(self, value: Any) -> str:
        """分组名：去空白 + 长度上限（空 = 未分组）。"""
        group = str(value or "").strip()
        if len(group) > SCREEN_MAX_GROUP:
            raise serializers.ValidationError(_("Group name is too long"))
        return group

    def validate_free_layout(self, value: Any) -> Any:
        """模板载荷：像素坐标 + 基准设计画幅（与自由画布同一套白名单与常量）。

        空模板（无窗格）拒绝——模板的语义就是「可复用的窗格骨架」。
        """
        panes = value.get("panes") if isinstance(value, dict) else None
        known_dashboards, known_datasets, datasets = layout_references(request_user(self), panes)
        try:
            free_layout = normalize_screen_template(value, known_dashboards, known_datasets)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        check_metric_value_fields(free_layout.get("panes") or [], datasets)
        return free_layout


class ScreenTemplateVersionSerializer(BaseModelSerializer):
    """模板历史版本（只读列表形态）：回滚目标选择用，不做写操作。"""

    ignore_field_permission = True

    class Meta:
        model = ScreenTemplateVersion
        fields = ["pk", "version", "name", "group", "free_layout", "created_time"]
        read_only_fields = fields


class ScreenVersionSerializer(BaseModelSerializer):
    """大屏布局历史版本（只读列表形态）：回滚目标选择用，不做写操作。"""

    ignore_field_permission = True

    class Meta:
        model = ScreenVersion
        fields = ["pk", "version", "layout", "layout_mode", "free_layout", "created_time"]
        read_only_fields = fields


class ScreenClipboardSerializer(BaseModelSerializer):
    """云端剪贴板（按用户单槽）：载荷与自由画布同形，允许为空（= 已清空）。"""

    ignore_field_permission = True

    class Meta:
        model = ScreenClipboard
        fields = ["pk", "free_layout", "updated_time"]
        read_only_fields = ["pk", "updated_time"]

    def validate_free_layout(self, value: Any) -> Any:
        """剪贴板载荷：与模板/自由画布同一份白名单；**允许空**（清空剪贴板）。"""
        panes = value.get("panes") if isinstance(value, dict) else None
        known_dashboards, known_datasets, datasets = layout_references(request_user(self), panes)
        try:
            return normalize_screen_clipboard(value, known_dashboards, known_datasets)
        except ScreenLayoutError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ScreenCommandSerializer(serializers.Serializer):
    """大屏远程控制指令：switch 需 dashboard_pk、page 需 index、refresh/auto 无参。"""

    command = serializers.ChoiceField(
        choices=[
            ("switch", _("Switch dashboard")),
            ("page", _("Go to page")),
            ("refresh", _("Refresh data")),
            ("auto", _("Resume carousel")),
        ],
        label=_("Command"),
    )
    dashboard_pk = serializers.CharField(required=False, allow_blank=True, default="", label=_("Dashboard"))
    index = serializers.IntegerField(required=False, min_value=0, label=_("Page index"))

    def validate(self, attrs: Any) -> Any:
        if attrs["command"] == "switch" and not attrs.get("dashboard_pk"):
            raise serializers.ValidationError(_("dashboard_pk is required for switch"))
        if attrs["command"] == "page" and attrs.get("index") is None:
            raise serializers.ValidationError(_("index is required for page"))
        return attrs
