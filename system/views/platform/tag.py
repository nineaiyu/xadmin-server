#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心视图：标签 CRUD + 打标 / 批量打标 + 白名单资源清单。

- 权限：标签管理 4 个权限点（list/create/partialUpdate/destroy:Tag）；
  打标（assign / batch-assign）回落业务对象的写权限点（``user_can_visit``
  与 AI 动作同一匹配函数，不新增对象级权限点；回落模板见 TAGGABLE_MODELS）；
  读取（objects）要求请求者对目标对象可见（可见域口径见 VISIBLE_QUERYSETS，
  不可见与不存在同响应 404，防主键探测）；
- 删除保护：内置标签不可删；被引用（有打标对象）的标签拒绝删除，提示先解绑；
- 过滤：列表 ``?tag=<id|name>``（多值 AND）落在对象视图集的 ``TagFilterBackend`` 上。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseViewSet,
    CreateAction,
    DestroyAction,
    DetailAction,
    ListAction,
    SearchColumnsAction,
    SearchFieldsAction,
    UpdateAction,
)
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.tag import Tag
from system.serializers.tag import TagAssignSerializer, TagBatchAssignSerializer, TagSerializer
from system.services.tags import (
    ensure_object_visible,
    ensure_tag_permission,
    invalidate_tag_options_cache,
    object_tags,
    set_object_tags,
    set_object_tags_batch,
    taggable_model,
    taggable_resources,
)


def _detail_of(exc: Exception) -> str:
    """服务层校验错误 → 可读文案（DRF 异常与 ApiResponse 两种出口共用）。"""
    return "; ".join(getattr(exc, "messages", None) or [str(exc)])


class TagFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = Tag
        fields = ["name", "builtin", "creator", "created_time"]


class TagViewSet(
    BaseViewSet,
    CreateAction,
    DestroyAction,
    UpdateAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """标签定义管理：CRUD + 使用计数 + 删除保护 + 打标端点。"""

    queryset = Tag.objects.annotate(usage_count=Count("tagged_items")).all()
    serializer_class = TagSerializer
    filterset_class = TagFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["name"]
    ordering_fields = ["name", "created_time", "updated_time"]
    select_related_fields = ("creator",)

    def perform_create(self, serializer: Any) -> Any:
        instance = super().perform_create(serializer)
        invalidate_tag_options_cache()
        return instance

    def perform_update(self, serializer: Any) -> Any:
        instance = super().perform_update(serializer)
        invalidate_tag_options_cache()
        return instance

    def perform_destroy(self, instance: Any) -> Any:
        if instance.builtin:
            # 内置标签不允许删除（与模型 docstring 同口径；误删可由 post_migrate 补回）
            raise RestValidationError(_("Builtin tags cannot be deleted"))
        used = instance.tagged_items.count()
        if used:
            # DRF 异常出口：删除保护是可读业务失败（400 + detail），不是 500
            raise RestValidationError(_("The tag is in use by {} objects; unbind it before deleting").format(used))
        result = super().perform_destroy(instance)
        invalidate_tag_options_cache()
        return result

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="resources")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def resources(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """可打标对象白名单（前端选择器数据源；非白名单对象不出现）。"""
        return ApiResponse(data={"resources": taggable_resources()})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="objects")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def objects(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """查询某对象的标签：``?resource=identity.userinfo&pk=<对象主键>``。"""
        model = taggable_model(request.query_params.get("resource"))
        if model is None:
            return ApiResponse(code=1001, detail=_("The object type cannot be tagged"))
        pk = request.query_params.get("pk")
        if pk:
            # 对象级校验：请求者看不到目标对象时不返回打标情况（与「对象不存在」同响应，
            # 避免知道主键就能探测任意对象的打标情况）；可见域口径见 VISIBLE_QUERYSETS
            ensure_object_visible(request.user, model, pk)
        return ApiResponse(data={"tags": object_tags(model, pk)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="assign")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def assign(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """单对象打标（全量替换语义）：权限回落业务对象 update 权限点。"""
        serializer = TagAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        model = taggable_model(payload["resource"])
        try:
            ensure_tag_permission(request.user, model, payload["pk"])
            tags = set_object_tags(model, payload["pk"], payload.get("tags") or [], request.user)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_detail_of(exc))
        return ApiResponse(data={"tags": tags}, detail=_("Tags updated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="batch-assign")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_assign(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量打标：``mode=replace|add|remove``（逐对象回落 update 权限点校验）。"""
        serializer = TagBatchAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        model = taggable_model(payload["resource"])
        changed, failed = set_object_tags_batch(
            model,
            payload["pks"],
            payload.get("tags") or [],
            mode=payload.get("mode") or "add",
            user=request.user,
            guard=lambda pk: ensure_tag_permission(request.user, model, pk),
        )
        detail = _("Tags updated: {} objects, {} failed").format(len(changed), len(failed))
        return ApiResponse(data={"success": changed, "failures": failed}, detail=detail)
