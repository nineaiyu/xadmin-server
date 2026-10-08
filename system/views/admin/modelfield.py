#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 1/5/2024

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.filter import BaseFilterSet
from common.core.modelset import ImportExportDataAction, ListDeleteModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import ModelLabelField
from system.serializers.field import ModelLabelFieldImportSerializer, ModelLabelFieldSerializer
from system.services.modelfield import (
    get_extra_field_lookups,
    get_field_lookup_info,
    get_field_meta,
    sync_model_field,
)
from system.utils.platform.rule_meta import MATCH_TEXTS, RULE_TYPE_GROUP_TEXTS, RULE_TYPE_META, RULE_TYPE_TEXTS

logger = get_logger(__name__)

# lookups 的 table 通配符：配置页用 * 代指用户模型（identity.userinfo），免传具体表名
LOOKUPS_USER_TABLE_WILDCARD = "*"


class ModelLabelFieldFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    label = filters.CharFilter(field_name="label", lookup_expr="icontains")
    parent = filters.CharFilter(field_name="parent", method="get_parent")

    def get_parent(self, queryset, name, value):
        if value == "0":
            return queryset.filter(parent=None)
        return queryset.filter(parent__id=value)

    class Meta:
        model = ModelLabelField
        fields = ["pk", "name", "label", "parent", "field_type", "created_time"]


class ModelLabelFieldViewSet(ListDeleteModelSet, ImportExportDataAction):
    """模型字段。

    导入导出与「展示序列化器全字段 read_only + 页面关闭行编辑」并存的口径说明：
    保留 ImportExportDataAction 是为与全站模型集能力对齐。导出用于字段元数据
    落档/对账；导入不是空转——import 动作经按 action 取序列化器的机制命中独立的
    ModelLabelFieldImportSerializer（未设 read_only，可写 name/label/parent/field_type，
    pk 与审计时间字段由框架自动只读），同步导入与异步任务重放（重放侧绑
    action=import_data）均走该序列化器，是与 sync 动作（按模型注册表自动装配）
    互补的手工批量维护入口（如跨环境迁移字段标签树）。展示序列化器全只读使
    视图自带的 create/update 端点无有效写入字段；前端字段管理页仅注册 sync
    权限码，RePlusPage 因此不渲染导入导出按钮，导入导出当前只有直连 API 入口。
    """

    queryset = ModelLabelField.objects.all()
    serializer_class = ModelLabelFieldSerializer
    pagination_class = DynamicPageNumber(1000)
    import_data_serializer_class = ModelLabelFieldImportSerializer
    ordering_fields = ["created_time", "updated_time"]
    filterset_class = ModelLabelFieldFilter

    @extend_schema(
        responses=get_default_response_schema(
            {
                "choices_dict": build_object_type(
                    properties={
                        "key": build_array_type(
                            build_object_type(
                                properties={
                                    "value": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="choices")
    def choices_dict(self, request, *args, **kwargs):
        """获取{cls}字段选择。

        规则类型的配置端元数据（值控件形态 / 是否必填 / 建议匹配符 / 分组）在此下发，
        配置页不再各自硬编码「类型 → 控件」映射，新增规则类型只需改 rule_meta。
        """
        result = get_choices_dict(ModelLabelField.KeyChoices.choices)
        for item in result:
            # 规则类型只显示「注入什么值」不够，补一条过滤语义说明供配置页展示
            item["hint"] = RULE_TYPE_TEXTS.get(item["value"], "")
            item.update(RULE_TYPE_META.get(item["value"], {}))
        # matches：匹配符中文文案（与预览解码 / 规则摘要同源）
        return ApiResponse(choices_dict={"choices": result, "groups": RULE_TYPE_GROUP_TEXTS, "matches": MATCH_TEXTS})

    @extend_schema(
        parameters=[
            OpenApiParameter(name="table", required=True, type=str),
            OpenApiParameter(name="field", required=True, type=str),
        ],
        responses=get_default_response_schema({"data": build_array_type(build_basic_type(OpenApiTypes.STR) or {})}),
    )
    @action(methods=["get"], detail=False, queryset=ModelLabelField.objects, filterset_class=None)
    def lookups(self, request, *args, **kwargs):
        """获取{cls}的字段名。

        返回该字段可用的匹配符（与读侧编译白名单同源）与字段形态元数据
        （field_meta：internal_type / 关联模型 / 是否多值），供配置页做控件适配与兼容性提示。
        """
        table = request.query_params.get("table")
        field = request.query_params.get("field")
        if table and field:
            if table == LOOKUPS_USER_TABLE_WILDCARD:
                table = "identity.userinfo"
            obj = (
                self.filter_queryset(self.get_queryset())
                .filter(name=field, parent__name=table, parent__parent=None)
                .first()
            )
            if obj:
                try:
                    mt = apps.get_model(table)
                    mf = mt._meta.get_field(field) if mt else None
                except (LookupError, FieldDoesNotExist):
                    # 未注册模型 / 字段名不存在：按无可用匹配符处理，不抛 500
                    mf = None
                if mf:
                    lookups = list(mf.get_class_lookups().keys()) + get_extra_field_lookups(mf)
                    return ApiResponse(data=get_field_lookup_info(lookups), field_meta=get_field_meta(mf))
        return ApiResponse(code=1001, detail=_("No lookups available for the field"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False)
    def sync(self, request, *args, **kwargs):
        """同步{cls}的字段名。

        全量同步有写副作用，只暴露 POST：GET 可被浏览器预取/代理重放误触发。
        """
        return ApiResponse(data=sync_model_field())
