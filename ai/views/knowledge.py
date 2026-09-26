#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 知识库文档管理视图：上传/预览/启停用/删除 + 仓库文档重建。"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from ai.models.ai import AiKnowledgeDocument
from ai.serializers.ai import AiKnowledgeDocumentSerializer, KnowledgeUploadSerializer
from ai.utils.ai import remove_chunks, set_document_active, sync_knowledge, upsert_upload_document
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


class AiKnowledgeDocumentFilter(BaseFilterSet):
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")
    path = filters.CharFilter(field_name="path", lookup_expr="icontains")

    class Meta:
        model = AiKnowledgeDocument
        fields = ["title", "path", "source_type", "is_active", "creator", "created_time"]


class AiKnowledgeDocumentViewSet(
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
    """批量删除（batch-destroy）自带 @action 定义：不走框架 BatchDestroyAction，
    因为需要逐条清理分块（见 batch_destroy 文档字符串）；同时避免装饰器叠加。"""

    """知识库文档管理：上传/预览/启用停用/删除 + 仓库文档重建。

    - 上传：{name, content} 文本入库（同名覆盖更新），前端选本地 .md 文件由浏览器读文本；
    - 预览：详情返回全文 + 分块摘要（列表轻量）；
    - 删除：仅 upload 来源（repo 由 sync 命令按文件存在性维护）；
    - sync-repo：管理端手动重新扫描仓库 docs/ 文档。
    """

    queryset = AiKnowledgeDocument.objects.all()
    serializer_class = AiKnowledgeDocumentSerializer
    filterset_class = AiKnowledgeDocumentFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-synced_at"]
    ordering_fields = ["synced_at", "created_time", "title"]
    select_related_fields = ("creator",)

    def create(self, request, *args, **kwargs):
        """上传文档（文本）：同名视为覆盖更新，重建分块后立即参与检索。"""
        serializer = KnowledgeUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc, created = upsert_upload_document(
            serializer.validated_data["name"], serializer.validated_data["content"], creator=request.user
        )
        return ApiResponse(
            data=self.get_serializer(doc).data,
            detail=_("Document uploaded") if created else _("Document updated"),
        )

    def perform_destroy(self, instance):
        """仅允许删除上传文档；仓库文档由同步命令随文件增删自动维护。"""
        if instance.source_type != AiKnowledgeDocument.SourceType.UPLOAD:
            raise ValidationError(_("Repository documents are managed by sync"))
        remove_chunks(instance.path)
        instance.delete()

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除：仅 upload（静默跳过 repo），逐条清理分块后删除。

        不复用框架批量删除：其非逐行分支走 queryset.delete()，不会触发
        perform_destroy，分块会残留成"孤儿块"继续参与检索。
        覆写必须自带 @action：DRF 按方法的 mapping 属性收集额外路由，
        无装饰器的覆写会导致该路由 405（Method Not Allowed）。
        """
        if not isinstance(request.data, (list, tuple)):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        queryset = self.filter_queryset(self.get_queryset()).filter(
            pk__in=request.data, source_type=AiKnowledgeDocument.SourceType.UPLOAD
        )
        count = 0
        for doc in queryset:
            remove_chunks(doc.path)
            doc.delete()
            count += 1
        return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(count))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "is_active": build_basic_type(OpenApiTypes.BOOL),
                },
                required=["pks", "is_active"],
                description="主键列表 + 目标启用状态",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-toggle")
    def batch_toggle(self, request, *args, **kwargs):
        """批量启用/停用：停用移除分块（退出问答检索），启用重建分块。"""
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            raise ValidationError(_("Please select the data to operate"))
        if "is_active" not in request.data:
            raise ValidationError(_("is_active is required"))
        want_active = bool(request.data.get("is_active"))
        changed = 0
        for doc in self.filter_queryset(self.get_queryset()).filter(pk__in=pks):
            if bool(doc.is_active) == want_active:
                continue
            set_document_active(doc, want_active)
            changed += 1
        return ApiResponse(
            data={"changed": changed},
            detail=_("Operation successful. Updated {} data").format(changed),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="sync-repo")
    def sync_repo(self, request, *args, **kwargs):
        """重新扫描仓库文档（docs/）并返回同步摘要（上传文档不受影响）。"""
        summary = sync_knowledge()
        return ApiResponse(data=summary, detail=_("Repository documents synced"))
