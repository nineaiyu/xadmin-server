#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 知识库文档管理视图：上传/预览/启停用/删除 + 仓库文档重建。"""

from typing import Any

from django.conf import settings
from django.db import transaction
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
from ai.utils.ai import remove_chunks, set_documents_active, upsert_upload_document
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
from common.core.throttle import AiThrottleMixin
from common.swagger.utils import get_default_response_schema


class AiKnowledgeDocumentFilter(BaseFilterSet):
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")
    path = filters.CharFilter(field_name="path", lookup_expr="icontains")

    class Meta:
        model = AiKnowledgeDocument
        fields = ["title", "path", "source_type", "is_active", "creator", "created_time"]


class AiKnowledgeDocumentViewSet(
    AiThrottleMixin,
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
    - sync-repo：管理端手动重新扫描仓库 docs/ 文档（后台任务，摘要经 sync-repo/status 轮询）。
    """

    queryset = AiKnowledgeDocument.objects.all()
    serializer_class = AiKnowledgeDocumentSerializer
    filterset_class = AiKnowledgeDocumentFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-synced_at"]
    ordering_fields = ["synced_at", "created_time", "title"]
    select_related_fields = ("creator",)

    #: 仓库全量同步与向量构建为高成本重操作：按管理类限流
    ai_admin_actions = ("sync_repo", "build_embeddings")

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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

    def perform_destroy(self, instance: Any) -> None:
        """仅允许删除上传文档；仓库文档由同步命令随文件增删自动维护。"""
        if instance.source_type != AiKnowledgeDocument.SourceType.UPLOAD:
            raise ValidationError(_("Repository documents are managed by sync"))
        remove_chunks(instance.path)
        instance.delete()

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR) or {})),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "is_active": build_basic_type(OpenApiTypes.BOOL),
                },
                required=["pks", "is_active"],
                description="主键列表 + 目标启用状态",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-toggle")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_toggle(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量启用/停用：停用移除分块（退出问答检索），启用重建分块。

        受影响文档合并为一次批量重建/清理（单次索引失效 + 至多一次向量补齐调度），
        不逐文档触发。
        """
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            raise ValidationError(_("Please select the data to operate"))
        if "is_active" not in request.data:
            raise ValidationError(_("is_active is required"))
        want_active = bool(request.data.get("is_active"))
        documents = [
            doc
            for doc in self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
            if bool(doc.is_active) != want_active
        ]
        set_documents_active(documents, want_active)
        return ApiResponse(
            data={"changed": len(documents)},
            detail=_("Operation successful. Updated {} data").format(len(documents)),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="sync-repo")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def sync_repo(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """提交仓库文档同步后台任务（上传文档不受影响）。

        全量重建为重操作，不再在请求线程内同步执行：响应返回任务提交信息，
        同步摘要（created/updated/removed/...）经 sync-repo/status 轮询获取。
        单飞：已有同步在跑时返回 1001（不排队、不重复扫盘）。
        提交即翻 running（先于 worker 拉起）：上一轮随通道保留的旧终态从提交
        时刻起不可命中，首轮轮询只会读到 running。
        """
        from ai.utils.sync_progress import mark_running, try_acquire_lock

        if not try_acquire_lock():
            return ApiResponse(code=1001, detail=_("A repository sync is already running"))
        mark_running()
        from ai.tasks import sync_repo_task

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # 测试/E2E：eager 下 apply_async 不执行，改 apply 同步跑完（与向量构建同口径）
            result = sync_repo_task.apply(args=[])
            task_id = str(result.id)
        else:
            transaction.on_commit(lambda: sync_repo_task.apply_async(args=[]))
            task_id = ""
        return ApiResponse(
            data={"task_id": task_id, "state": "running", "status_url": "sync-repo/status"},
            detail=_("Repository sync task submitted"),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="sync-repo/status")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def sync_repo_status(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """仓库文档同步运行状态（轮询端点）：state + 终态同步摘要。"""
        from ai.utils.sync_progress import get_status

        return ApiResponse(data=get_status())

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="vector-status")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def vector_status(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """向量通道状态：是否启用（embedding 档案）/ 模型 / 维度 / 已构建与陈旧条数。

        未配置 embedding 档案时 ``enabled=false``，检索完全走词频（零变化）。
        """
        from ai.utils.ai_embeddings import vector_stats

        return ApiResponse(data=vector_stats())

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "force": build_basic_type(OpenApiTypes.BOOL),
                    "document": build_basic_type(OpenApiTypes.UUID),
                },
                description="force=全量重算；document=限定单个文档（缺省全库）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="build-embeddings")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def build_embeddings(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """提交向量构建后台任务（7.3 异步化；进度经 build-embeddings/status 轮询）。

        单飞：已有构建在跑时返回 1001 + 当前状态（不排队、不重复消耗供应商预算）；
        未配置 embedding 档案直接拒绝并给出引导。终态摘要随状态通道保留 1 小时。
        提交即翻 running（先于 worker 拉起）：上一轮随通道保留的旧终态从提交
        时刻起不可命中，首轮轮询只会读到 running。
        """
        from ai.utils.ai_config import embedding_credentials
        from ai.utils.embedding_progress import get_status, mark_running, try_acquire_lock

        if embedding_credentials() is None:
            return ApiResponse(code=1001, detail=_("No active embedding profile is configured"))
        if not try_acquire_lock():
            return ApiResponse(
                code=1001,
                detail=_("An embedding build is already running"),
                data=get_status(),
            )
        document_pk = ""
        document = None
        document_param = request.data.get("document")
        if document_param:
            document = self.get_queryset().filter(pk=document_param).first()
            if document is None:
                from ai.utils.embedding_progress import release_lock

                release_lock()
                raise ValidationError(_("Document does not exist"))
            document_pk = str(document.pk)
        force = bool(request.data.get("force"))

        from ai.tasks import build_embeddings_task

        mark_running(0)
        task_args = [document_pk, force]
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # 测试/E2E：eager 下 apply_async 不执行，改 apply 同步跑完（与导出同口径）
            result = build_embeddings_task.apply(args=task_args)
            task_id = str(result.id)
        else:
            transaction.on_commit(lambda: build_embeddings_task.apply_async(args=task_args))
            task_id = ""
        return ApiResponse(
            data={"task_id": task_id, "state": "running", "status_url": "build-embeddings/status"},
            detail=_("Embedding build task submitted"),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="build-embeddings/status")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def build_embeddings_status(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """向量构建运行状态（轮询端点）：state/percent/stage + 终态摘要。"""
        from ai.utils.embedding_progress import get_status

        return ApiResponse(data=get_status())
