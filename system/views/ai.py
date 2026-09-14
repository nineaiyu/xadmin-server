#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手视图：配置（Setting 体系）+ 状态 + 问答。

- 配置视图与邮件/LDAP 同构：POST create = 连接测试（真实 ping LLM）；
- ask/status 经菜单权限点门控（未授权 403）；问答链路不触生产数据。
"""

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
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
from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from settings.serializers.ai import AiAssistantSettingSerializer
from settings.views.settings import BaseSettingViewSet
from system.models.ai import AiKnowledgeChunk, AiKnowledgeDocument, AiProfile
from system.models.dataset import Dataset
from system.serializers.ai import AiKnowledgeDocumentSerializer, AiProfileSerializer, KnowledgeUploadSerializer
from system.utils.ai import (
    ai_credentials,
    ask,
    is_configured,
    is_enabled,
    profile_credentials,
    remove_chunks,
    set_active_profile,
    set_document_active,
    sync_knowledge,
    upsert_upload_document,
)

logger = get_logger(__name__)


class AiAssistantSettingViewSet(BaseSettingViewSet):
    """AI 助手配置与连接测试"""

    serializer_class = AiAssistantSettingSerializer
    category = "ai"

    def create(self, request, *args, **kwargs):
        """测试{cls}：按表单当前值实际 ping 一次 LLM。"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        keys = ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT")
        saved = {key: getattr(settings, key) for key in keys}
        try:
            for key in ("AI_BASE_URL", "AI_MODEL", "AI_TIMEOUT"):
                if key in request.data:
                    setattr(settings, key, data.get(key))
            api_key = data.get("AI_API_KEY") or settings.AI_API_KEY
            if not settings.AI_BASE_URL:
                return ApiResponse(code=1001, detail=_("Base URL is required"))
            if not (api_key and settings.AI_MODEL):
                return ApiResponse(code=1001, detail=_("API Key and model are required"))
            original_key = settings.AI_API_KEY
            settings.AI_API_KEY = api_key
            try:
                client = ChatCompletionsClient(ai_credentials())
                reply = client.chat([{"role": "user", "content": "ping"}])
            finally:
                settings.AI_API_KEY = original_key
        except AiSdkError as exc:
            return ApiResponse(code=1002, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 测试入口兜底
            logger.warning("AI connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)
        return ApiResponse(detail=_("AI provider OK: {}").format(reply[:80]))


class AiAssistantViewSet(GenericViewSet):
    """AI 使用/二开助手（基于 docs/ 知识库的 RAG 问答，不触生产数据）"""

    queryset = AiKnowledgeChunk.objects.none()

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="status")
    def status(self, request, *args, **kwargs):
        """助手状态：开关/配置/知识库规模（前端渲染未配置引导）。"""
        last_synced = AiKnowledgeChunk.objects.order_by("-synced_at").values_list("synced_at", flat=True).first()
        return ApiResponse(
            data={
                "enabled": is_enabled(),
                "configured": is_configured(),
                "chunks": AiKnowledgeChunk.objects.count(),
                "synced_at": last_synced.isoformat() if last_synced else "",
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/interpret")
    def nl_interpret(self, request, *args, **kwargs):
        """NL → 数据集 DSL（白名单校验）+ 试算预览计数（数据权限随调用者）。"""
        from common.sdk.ai.chat import AiSdkError
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.nl_query import (
            audit_nl_query,
            build_interpret_prompt,
            parse_llm_json,
            validate_dsl,
            visible_datasets,
        )

        question = str(request.data.get("question") or "").strip()
        if not question:
            return ApiResponse(code=1001, detail=_("Question cannot be empty"))
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))

        datasets = visible_datasets(request.user)
        if not datasets:
            return ApiResponse(code=1001, detail=_("No visible datasets for NL query"))

        dsl: dict = {}
        normalized: dict = {}
        try:
            client = ChatCompletionsClient(ai_credentials())
            raw = client.chat(build_interpret_prompt(question, datasets))
            dsl = parse_llm_json(raw)
            normalized = validate_dsl(dsl, request.user)
            dataset = Dataset.objects.get(pk=normalized["dataset"])
            extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
            from system.utils.dataset import build_queryset

            queryset, model, __ = build_queryset(dataset, request.user, extra_filters=extra)
            preview_count = queryset.count()
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "interpret", question, dsl, error="; ".join(exc.messages))
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        except AiSdkError as exc:
            audit_nl_query(request.user, "interpret", question, {}, error=str(exc))
            return ApiResponse(code=1001, detail=str(exc))
        audit_nl_query(request.user, "interpret", question, normalized, rows=preview_count)
        return ApiResponse(
            data={
                "dsl": normalized,
                "dataset_name": dataset.name,
                "preview_count": preview_count,
                "mode": normalized["mode"],
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="nl-query/run")
    def nl_run(self, request, *args, **kwargs):
        """执行试算确认后的 DSL：服务端重校验（不信任客户端回传）+ 审计。"""
        from system.utils.ai import is_enabled as ai_enabled_check
        from system.utils.dataset import aggregate_dataset
        from system.utils.nl_query import audit_nl_query, validate_dsl

        dsl = request.data.get("dsl")
        if not settings.AI_NL_QUERY_ENABLED:
            return ApiResponse(code=1001, detail=_("NL query is not enabled"))
        if not ai_enabled_check():
            return ApiResponse(code=1001, detail=_("AI assistant is not enabled or configured"))
        try:
            normalized = validate_dsl(dsl if isinstance(dsl, dict) else {}, request.user)
            dataset = Dataset.objects.get(pk=normalized["dataset"])
            if normalized["mode"] == "aggregate":
                result = aggregate_dataset(
                    dataset,
                    request.user,
                    group_by=normalized["group_by"],
                    metric=normalized["metric"],
                    date_trunc=normalized["date_trunc"] or None,
                    value_field=normalized["value_field"] or None,
                )
                rows = len(result["series"])
            else:
                from system.utils.dataset import build_queryset

                extra = [{"field": f["field"], "op": f["op"], "value": f["value"]} for f in normalized["filters"]]
                queryset, model, columns = build_queryset(dataset, request.user, extra_filters=extra)
                result = {"columns": columns, "rows": list(queryset.values(*columns)[: normalized["limit"]])}
                rows = len(result["rows"])
        except DjangoValidationError as exc:
            audit_nl_query(request.user, "run", "", dsl if isinstance(dsl, dict) else {}, error="; ".join(exc.messages))
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        audit_nl_query(request.user, "run", "", normalized, rows=rows)
        return ApiResponse(data=result)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="ask")
    def ask(self, request, *args, **kwargs):
        """文档问答：回答引用文档出处；未启用/未配置/无命中/LLM 失败均转可读文案。"""
        question = str(request.data.get("question") or "")
        try:
            result = ask(question)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail="; ".join(exc.messages))
        return ApiResponse(data=result)


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


class AiProfileFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    model = filters.CharFilter(field_name="model", lookup_expr="icontains")

    class Meta:
        model = AiProfile
        fields = ["name", "model", "is_active", "creator", "created_time"]


class AiProfileViewSet(
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
    """AI 配置档案：多套凭据 + 采样/行为参数，至多一个激活。

    - 激活（activate）后供全部 AI 链路使用；删除激活行 / 停用（deactivate）
      后回落 Setting 体系历史配置（category=ai）；
    - api_key 明文只进不出（加密落库，回显 api_key_set 布尔）；
    - test：按档案当前持久化值真实 ping 一次 LLM。
    """

    queryset = AiProfile.objects.all()
    serializer_class = AiProfileSerializer
    filterset_class = AiProfileFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-is_active", "name"]
    ordering_fields = ["name", "is_active", "updated_time", "created_time"]
    select_related_fields = ("creator",)

    def perform_create(self, serializer):
        # 先清激活行再插入：部分唯一索引（uniq_ai_profile_active）下
        # 「带 is_active=true 直接新建」才不会在插入瞬间撞约束
        from django.db import transaction

        with transaction.atomic():
            if serializer.validated_data.get("is_active"):
                AiProfile.objects.filter(is_active=True).update(is_active=False)
            instance = serializer.save()
            if instance.is_active:
                set_active_profile(instance, True)

    def perform_update(self, serializer):
        from django.db import transaction

        with transaction.atomic():
            if serializer.validated_data.get("is_active"):
                AiProfile.objects.exclude(pk=serializer.instance.pk).filter(is_active=True).update(is_active=False)
            instance = serializer.save()
            if instance.is_active:
                set_active_profile(instance, True)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="activate")
    def activate(self, request, *args, **kwargs):
        """激活档案（事务内清掉其余激活行，全局至多一个激活档案）。"""
        set_active_profile(self.get_object(), True)
        return ApiResponse(detail=_("Profile activated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="deactivate")
    def deactivate(self, request, *args, **kwargs):
        """停用档案：AI 全链路回落 Setting 体系历史配置。"""
        set_active_profile(self.get_object(), False)
        return ApiResponse(detail=_("Profile deactivated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="test")
    def test(self, request, *args, **kwargs):
        """按档案持久化值真实 ping 一次 LLM（api_key 用已存密钥）。"""
        profile = self.get_object()
        if not profile.is_configured:
            return ApiResponse(code=1001, detail=_("Fill in base URL, API key and model before testing"))
        try:
            reply = ChatCompletionsClient(profile_credentials(profile)).chat([{"role": "user", "content": "ping"}])
        except AiSdkError as exc:
            return ApiResponse(code=1002, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 测试入口兜底
            logger.warning("AI profile test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        return ApiResponse(detail=_("AI provider OK: {}").format(reply[:80]))
