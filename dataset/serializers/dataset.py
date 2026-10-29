#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集与仪表盘序列化器。"""

from typing import Any

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from dataset.models.dataset import Dashboard, Dataset
from dataset.utils.dataset import (
    filter_layout_for_user,
    numeric_columns_of,
    validate_dataset,
    visible_dataset_queryset,
)

ALLOWED_CHART_TYPES = ("number", "metric", "line", "bar", "pie")
#: 仪表盘布局上限：单条 PATCH 可写数 MB layout，读侧 filter_layout_for_user
#: 逐卡遍历——按首屏可用性与读侧成本收敛到一屏卡片量级（与大屏窗格上限同思路）
DASHBOARD_MAX_CARDS = 24
DASHBOARD_MAX_TITLE = 64
#: 卡片宽度档位（与前端 12 栅格 span 表单一致）
ALLOWED_CARD_SPANS = (3, 6, 9, 12)
#: 卡片高度档位（与前端 cardHeight.ts 的 CARD_HEIGHT_OPTIONS 对齐；未配置由读取侧回退 224，后端不代填）
ALLOWED_CARD_HEIGHTS = (160, 224, 320, 440)
#: 卡片 id 与角色 code 的单项长度上限（与标题同一量级，防超长串借 layout 入库）
CARD_MAX_ID_LENGTH = 64
CARD_MAX_ROLE_CODE_LENGTH = 64
#: 单卡片 allowed_roles 数量上限（与文件内其余 64 容量口径一致，远超单卡实际角色分组粒度）
CARD_MAX_ROLES = 64
#: 卡片键白名单（契约真源：前端 DashboardCard 类型；消费口径见 dashboard_data / screen_data）
DASHBOARD_CARD_KEYS = (
    "id",
    "dataset",
    "title",
    "chart_type",
    "group_by",
    "metric",
    "date_trunc",
    "value_field",
    "span",
    "height",
    "allowed_roles",
)


def _normalized_card_roles(raw: Any) -> list[str]:
    """allowed_roles 收敛：类型/单项长度/数量校验后保序去重（重复角色码不算错误）。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise serializers.ValidationError(_("Invalid card roles"))
    over_length = any(len(item) > CARD_MAX_ROLE_CODE_LENGTH for item in raw)
    if over_length or len(raw) > CARD_MAX_ROLES:
        raise serializers.ValidationError(_("Invalid card roles"))
    return list(dict.fromkeys(raw))


def _pure_card_keys(card: dict[str, Any]) -> dict[str, Any]:
    """按白名单重建卡片键：契约外键静默剔除（不报错，向前兼容前端增量字段演进）。"""
    return {key: card[key] for key in DASHBOARD_CARD_KEYS if key in card}


class DatasetSerializer(BaseModelSerializer):
    # 定义类资源（配置对象）不做字段权限裁剪：可见性语义 = 创建者/共享；
    # 字段权限叠加发生在执行/聚合输出侧（dataset/utils/dataset.py）
    ignore_field_permission = True
    # 非创建者修改/删除被写守卫拒绝（1003）：下发 is_owner 供行内按钮显隐
    row_owner_guard = True
    # 数值列（读侧派生）：卡片/报表的 sum・avg 度量字段候选（后端聚合会做同样校验）
    numeric_columns = serializers.SerializerMethodField(label=_("Numeric columns"))
    # 关联计数声明：报表引用数（与影响面同源——「删除会影响几张报表」的同一口径）
    relation_count_fields = {"report_count": Count("report")}
    report_count = serializers.SerializerMethodField(label=_("Report count"))

    class Meta:
        model = Dataset
        fields = [
            "pk",
            "name",
            "description",
            "bound_model",
            "columns",
            "filters",
            "ordering",
            "row_limit",
            "config",
            "visibility",
            "field_permission_mode",
            "numeric_columns",
            "report_count",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "created_time", "updated_time"]
        # RePlusPage 列表列：列/filters/ordering/config 等定义细节不进列表
        table_fields = ["name", "bound_model", "visibility", "report_count", "description", "updated_time"]

    def get_numeric_columns(self, obj: Any) -> list[Any]:
        return numeric_columns_of(obj)

    @extend_schema_field(serializers.IntegerField)
    def get_report_count(self, obj: Any) -> Any:
        count = getattr(obj, "report_count", None)
        return count if count is not None else obj.report_set.count()

    def validate(self, attrs: Any) -> Any:
        """保存侧白名单校验：部分更新时与既有实例字段合并后整体校验。

        新建/切换为共享且未显式选择模式时默认 strict：共享数据集面向未配置字段权限
        的角色时 fail-closed；个人数据集保持 inherit，避免打空个人仪表盘。
        """
        visibility = attrs.get("visibility", getattr(self.instance, "visibility", None))
        if (
            visibility == Dataset.Visibility.SHARED
            and "field_permission_mode" not in self.initial_data
            and (self.instance is None or self.instance.visibility != Dataset.Visibility.SHARED)
        ):
            attrs["field_permission_mode"] = Dataset.FieldPermissionMode.STRICT
        merged = {
            "bound_model": attrs.get("bound_model", getattr(self.instance, "bound_model", "")),
            "columns": attrs.get("columns", getattr(self.instance, "columns", [])),
            "filters": attrs.get("filters", getattr(self.instance, "filters", [])),
            "ordering": attrs.get("ordering", getattr(self.instance, "ordering", "")),
            "row_limit": attrs.get("row_limit", getattr(self.instance, "row_limit", 1000)),
            "config": attrs.get("config", getattr(self.instance, "config", {})),
        }
        validate_dataset(Dataset(**merged))
        return attrs


class DashboardSerializer(BaseModelSerializer):
    # 同 DatasetSerializer：定义类资源豁免字段权限；非创建者写守卫 1003 → 下发 is_owner
    ignore_field_permission = True
    row_owner_guard = True

    class Meta:
        model = Dashboard
        fields = ["pk", "name", "layout", "visibility", "created_time", "updated_time"]
        read_only_fields = ["pk", "created_time", "updated_time"]

    def validate_layout(self, value: Any) -> Any:
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid dashboard layout"))
        if len(value) > DASHBOARD_MAX_CARDS:
            raise serializers.ValidationError(_("Too many dashboard cards (max {})").format(DASHBOARD_MAX_CARDS))
        # 数据集引用按保存者可见域收敛（visible_dataset_queryset：personal 仅创建者），
        # 代替全表存在性校验——否则低权用户可借「Unknown dataset」差异探测他人个人数据集
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is not None:
            dataset_pks = {str(pk) for pk in visible_dataset_queryset(viewer).values_list("pk", flat=True)}
        else:
            dataset_pks = {str(pk) for pk in Dataset.objects.values_list("pk", flat=True)}
        from identity.models import UserRole

        known_role_codes = set(UserRole.objects.values_list("code", flat=True))
        dataset_cache: dict[str, Dataset] = {}
        for card in value or []:
            if not isinstance(card, dict) or not card.get("id") or not card.get("dataset"):
                raise serializers.ValidationError(_("Invalid dashboard layout"))
            if not isinstance(card.get("id"), str) or len(card["id"]) > CARD_MAX_ID_LENGTH:
                raise serializers.ValidationError(_("Invalid dashboard layout"))
            title = card.get("title")
            if title is not None and (not isinstance(title, str) or len(title) > DASHBOARD_MAX_TITLE):
                raise serializers.ValidationError(_("Invalid dashboard card title"))
            span = card.get("span")
            if span is not None and span not in ALLOWED_CARD_SPANS:
                raise serializers.ValidationError(_("Invalid dashboard card span"))
            height = card.get("height")
            if height is not None and height not in ALLOWED_CARD_HEIGHTS:
                raise serializers.ValidationError(_("Invalid dashboard card height"))
        for card in value or []:
            if not isinstance(card, dict) or not card.get("id") or not card.get("dataset"):
                raise serializers.ValidationError(_("Invalid dashboard layout"))
            if card.get("chart_type", "number") not in ALLOWED_CHART_TYPES:
                raise serializers.ValidationError(_("Invalid chart type: {}").format(card.get("chart_type")))
            dataset_pk = str(card["dataset"])
            if dataset_pk not in dataset_pks:
                raise serializers.ValidationError(_("Unknown dataset in layout"))
            dataset = dataset_cache.get(dataset_pk)
            if dataset is None:
                dataset = Dataset.objects.get(pk=dataset_pk)
                dataset_cache[dataset_pk] = dataset
            metric = card.get("metric", "count")
            if metric not in ("count", "sum", "avg"):
                raise serializers.ValidationError(_("Invalid metric: {}").format(metric))
            if metric in ("sum", "avg") and card.get("value_field") not in numeric_columns_of(dataset):
                raise serializers.ValidationError(
                    _("Value field is not numeric in dashboard card: {}").format(card["id"])
                )
            group_by = card.get("group_by")
            if group_by and group_by not in (dataset.columns or []):
                raise serializers.ValidationError(_("Group field is not in dataset columns: {}").format(card["id"]))
            # 卡片级权限（allowed_roles）：空 = 全员可见；非空须为已知角色 code。
            # 未配置时不回填空列表，保持入库形状与保存内容一致
            if card.get("allowed_roles") is not None:
                allowed_roles = _normalized_card_roles(card["allowed_roles"])
                unknown = [code for code in allowed_roles if code not in known_role_codes]
                if unknown:
                    raise serializers.ValidationError(_("Unknown role codes: {}").format(", ".join(unknown)))
                card["allowed_roles"] = allowed_roles
        # 键白名单化入库：契约外键剔除（静默，见 _pure_card_keys），保证存储形状与契约一致
        return [_pure_card_keys(card) for card in value]

    def to_representation(self, instance: Any) -> Any:
        """读取侧按浏览者过滤卡片（卡片级权限；超管全量，匿名 fail-closed）。"""
        data = super().to_representation(instance)
        request = self.context.get("request")
        data["layout"] = filter_layout_for_user(
            instance.layout,
            getattr(request, "user", None),
            owner_id=getattr(instance, "creator_id", None),
        )
        return data
