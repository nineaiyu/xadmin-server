#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心（P-1）视图：标签 CRUD + 打标 / 批量打标 + 白名单资源清单。

- 权限：标签管理 4 个权限点（list/create/partialUpdate/destroy:Tag）；
  打标（assign / batch-assign）回落业务对象的 update 权限点（``user_can_visit``
  与 AI 动作同一匹配函数，不新增对象级权限点）；
- 删除保护：被引用（有打标对象）的标签拒绝删除，提示先解绑；
- 过滤：列表 ``?tag=<id|name>``（多值 AND）落在对象视图集的 ``TagFilterBackend`` 上。
"""

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
from system.utils.tags import (
    ensure_tag_permission,
    invalidate_tag_options_cache,
    object_tags,
    set_object_tags,
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

    def perform_create(self, serializer):
        instance = super().perform_create(serializer)
        invalidate_tag_options_cache()
        return instance

    def perform_update(self, serializer):
        instance = super().perform_update(serializer)
        invalidate_tag_options_cache()
        return instance

    def perform_destroy(self, instance):
        used = instance.tagged_items.count()
        if used:
            # DRF 异常出口：删除保护是可读业务失败（400 + detail），不是 500
            raise RestValidationError(_("The tag is in use by {} objects; unbind it before deleting").format(used))
        result = super().perform_destroy(instance)
        invalidate_tag_options_cache()
        return result

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="resources")
    def resources(self, request, *args, **kwargs):
        """可打标对象白名单（前端选择器数据源；非白名单对象不出现）。"""
        return ApiResponse(data={"resources": taggable_resources()})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="objects")
    def objects(self, request, *args, **kwargs):
        """查询某对象的标签：``?resource=system.userinfo&pk=<对象主键>``。"""
        model = taggable_model(request.query_params.get("resource"))
        if model is None:
            return ApiResponse(code=1001, detail=_("The object type cannot be tagged"))
        return ApiResponse(data={"tags": object_tags(model, request.query_params.get("pk"))})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="assign")
    def assign(self, request, *args, **kwargs):
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
    @action(methods=["post"], detail=False, url_path="batch-assign")
    def batch_assign(self, request, *args, **kwargs):
        """批量打标：``mode=replace|add|remove``（逐对象回落 update 权限点校验）。"""
        serializer = TagBatchAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        model = taggable_model(payload["resource"])
        mode = payload.get("mode") or "add"
        changed, failed = [], []
        for pk in payload["pks"]:
            try:
                ensure_tag_permission(request.user, model, pk)
                current = [] if mode == "replace" else [item["pk"] for item in object_tags(model, pk)]
                incoming = [str(item) for item in payload.get("tags") or []]
                if mode == "add":
                    wanted = list(dict.fromkeys([*current, *incoming]))
                elif mode == "remove":
                    wanted = [item for item in current if item not in set(incoming)]
                else:
                    wanted = incoming
                tags = set_object_tags(model, pk, wanted, request.user)
                changed.append({"pk": str(pk), "tags": tags})
            except DjangoValidationError as exc:
                failed.append({"pk": str(pk), "reason": _detail_of(exc)})
        detail = _("Tags updated: {} objects, {} failed").format(len(changed), len(failed))
        return ApiResponse(data={"success": changed, "failures": failed}, detail=detail)
