#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 配置档案视图：多套凭据 + 采样/行为参数，至多一个激活。"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from ai.models.ai import AiProfile
from ai.serializers.ai import AiProfileSerializer
from ai.utils.ai import profile_credentials, set_active_profile
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

logger = get_logger(__name__)


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

    - 激活（activate）后供对应用途的 AI 链路使用；删除激活行 / 停用（deactivate）
      后回落 Setting 体系历史配置（category=ai）；
    - 用途（purpose）分流：chat 供问答/聊天、structured 供 NL 查数/动作草稿；
      每种用途至多一个激活档案，未配 structured 时结构化链路回落 chat 档案；
    - api_key 明文只进不出（加密落库，回显 api_key_set 布尔）；
    - test：按档案当前持久化值真实 ping 一次 LLM；
    - probe：按序探测 JSON / tool_calls / reasoning（可选 vision）四项能力并落 capabilities。
    """

    queryset = AiProfile.objects.all()
    serializer_class = AiProfileSerializer
    filterset_class = AiProfileFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-is_active", "name"]
    ordering_fields = ["name", "is_active", "updated_time", "created_time"]
    select_related_fields = ("creator",)

    def perform_create(self, serializer):
        # 先清同用途激活行再插入：用途级部分唯一索引（uniq_ai_profile_purpose_active）下
        # 「带 is_active=true 直接新建」才不会在插入瞬间撞约束
        from django.db import transaction

        with transaction.atomic():
            if serializer.validated_data.get("is_active"):
                purpose = serializer.validated_data.get("purpose") or AiProfile.Purpose.CHAT
                AiProfile.objects.filter(is_active=True, purpose=purpose).update(is_active=False)
            instance = serializer.save()
            if instance.is_active:
                set_active_profile(instance, True)

    def perform_update(self, serializer):
        from django.db import transaction

        with transaction.atomic():
            if serializer.validated_data.get("is_active"):
                purpose = serializer.validated_data.get("purpose") or serializer.instance.purpose
                AiProfile.objects.exclude(pk=serializer.instance.pk).filter(is_active=True, purpose=purpose).update(
                    is_active=False
                )
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

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="probe")
    def probe(self, request, *args, **kwargs):
        """能力探测：按序验证 JSON / tool_calls / reasoning（可选 vision）并落 capabilities。

        - 请求体可选 ``capabilities``（能力子集）与 ``vision``（是否追加多模态探测）；
        - 探测不阻断：单项失败只记录 ok=False + 可读原因，返回结果供前端提示与人工修正；
        - 结果写回档案（capabilities + probed_at），可 PATCH 手工覆盖。
        """
        from django.utils import timezone

        from ai.utils.ai_probe import ALL_CAPABILITIES, probe_profile

        profile = self.get_object()
        if not profile.is_configured:
            return ApiResponse(code=1001, detail=_("Fill in base URL, API key and model before testing"))
        requested = request.data.get("capabilities")
        requested = [str(item) for item in requested] if isinstance(request.data.get("capabilities"), list) else None
        if requested:
            unknown = [name for name in requested if name not in ALL_CAPABILITIES]
            if unknown:
                return ApiResponse(code=1001, detail=_("Unknown capability: {}").format(", ".join(unknown[:5])))
        try:
            result = probe_profile(profile, capabilities=requested, vision=bool(request.data.get("vision")))
        except Exception as exc:  # noqa: BLE001 探测入口兜底（凭据/网络异常归一可读文案）
            logger.warning("AI profile probe failed", exc_info=True)
            return ApiResponse(code=1002, detail=str(exc))
        profile.capabilities = result
        profile.probed_at = timezone.now()
        profile.save(update_fields=["capabilities", "probed_at", "updated_time"])
        return ApiResponse(data=result, detail=_("Model probe finished"))
