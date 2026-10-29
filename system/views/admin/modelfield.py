#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 1/5/2024
import uuid
from typing import Any

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.base.crypto import get_choices_dict
from common.core.filter import BaseFilterSet
from common.core.modelset import (
    DetailAction,
    ListAction,
    OnlyExportDataAction,
    SearchColumnsAction,
    SearchFieldsAction,
)
from common.core.modelset.base import BaseViewSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import ModelLabelField
from system.serializers.field import ModelLabelFieldSerializer
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

    def get_parent(self, queryset: Any, name: Any, value: Any) -> Any:
        if value == "0":
            return queryset.filter(parent=None)
        # 兼容两种入参：父级主键（UUID 精确）与父级名称（模糊搜索）
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return queryset.filter(parent__name__icontains=value)
        return queryset.filter(parent__id=value)

    class Meta:
        model = ModelLabelField
        fields = ["pk", "name", "label", "parent", "field_type", "created_time"]


class ModelLabelFieldViewSet(
    BaseViewSet,
    # OnlyExportDataAction 继承 ListAction：必须排在 ListAction 之前，否则 MRO 冲突
    OnlyExportDataAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """模型字段

    只读台账：列表 / 详情 / 导出，**不提供** create / update / delete / import。
    历史上继承 ImportExportDataAction（自带 Create/Update 动作）时，展示序列化器
    虽全字段只读，空表单 POST 仍会落到 Django 默认值，在字段注册表里生成
    ``name='' label=''`` 的空行（污染字段树与权限白名单）。删除同样关闭：批量删除
    会把字段从所有角色的字段权限白名单里连带抹掉（M2M 级联），而页面没有任何影响面
    提示。字段的维护入口只有两条且都不经过本视图的写动作：``sync``（按模型注册表
    自动装配，含过期行修剪）与 ``sync_field_permissions``（字段树 + 白名单）。
    """

    queryset = ModelLabelField.objects.all()
    serializer_class = ModelLabelFieldSerializer
    pagination_class = DynamicPageNumber(1000)
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
    @action(methods=["get"], detail=False, url_path="choices")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def choices_dict(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["get"], detail=False, queryset=ModelLabelField.objects, filterset_class=None)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def lookups(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的字段名。

        返回该字段可用的匹配符（与读侧编译白名单同源）与字段形态元数据
        （field_meta：internal_type / 关联模型 / 是否多值），供配置页做控件适配与兼容性提示。
        """
        table = request.query_params.get("table")
        field = request.query_params.get("field")
        if not (table and field):
            return ApiResponse(code=1001, detail=_("No lookups available for the field"))
        if table == LOOKUPS_USER_TABLE_WILDCARD:
            table = "identity.userinfo"
        obj = (
            self.filter_queryset(self.get_queryset())
            .filter(
                name=field,
                parent__name=table,
                parent__parent=None,
                # 只允许数据权限注册表中的模型根节点，避免借 lookups 枚举任意
                # 已注册模型（含未授权业务模型）的字段与关联关系
                parent__field_type=ModelLabelField.FieldChoices.DATA,
            )
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
        # 字段未同步/已下线：返回空候选（前端展示「无匹配符」），
        # 不再用 1001 让消费方只能静默吞掉
        return ApiResponse(data=[], field_meta=None)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def sync(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """同步{cls}的字段名。

        全量同步有写副作用，只暴露 POST：GET 可被浏览器预取/代理重放误触发。
        """
        return ApiResponse(data=sync_model_field())
