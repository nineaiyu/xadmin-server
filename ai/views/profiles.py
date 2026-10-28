#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 配置档案视图：多套凭据 + 采样/行为参数，至多一个激活。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from ai.models.ai import AiProfile
from ai.serializers.ai import AiProfileSerializer
from ai.services.assistant.ai import set_active_profile
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
from common.utils import get_logger

logger = get_logger(__name__)


class AiProfileFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    model = filters.CharFilter(field_name="model", lookup_expr="icontains")

    class Meta:
        model = AiProfile
        fields = ["name", "model", "is_active", "creator", "created_time"]


class AiProfileViewSet(
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
    """AI 配置档案

    多套凭据 + 采样/行为参数，至多一个激活。

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

    #: test / probe 均外呼供应商（probe 多次调用）：按管理类重操作限流
    ai_admin_actions = ("test", "probe")

    def perform_create(self, serializer: Any) -> None:
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

    def perform_update(self, serializer: Any) -> None:
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
    @action(methods=["post"], detail=True, url_path="activate")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def activate(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """激活档案

        同一 purpose 内至多一个激活档案（chat / structured / embedding 各自独立）；
        全局激活数可大于一，按用途分别清理。
        """
        set_active_profile(self.get_object(), True)
        return ApiResponse(detail=_("Profile activated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="deactivate")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def deactivate(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """停用档案

        AI 全链路回落 Setting 体系历史配置
        """
        set_active_profile(self.get_object(), False)
        return ApiResponse(detail=_("Profile deactivated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="test")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def test(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """档案连通性探测

        api_key 用已存密钥；外呼下沉 celery，返回 task_id 供 test-result 回读
        """
        from ai.tasks import test_ai_profile_task

        profile = self.get_object()
        if not profile.is_configured:
            return ApiResponse(code=1001, detail=_("Fill in base URL, API key and model before testing"))
        task = test_ai_profile_task.delay(str(profile.pk))
        payload: dict[str, Any] = {"task_id": task.id, "state": task.state}
        # eager（测试 / E2E / CELERY_TASK_ALWAYS_EAGER）下任务已就地跑完：结果直接带回，
        # 前端不必再轮询（这类环境没有结果后端，AsyncResult 永远读不到结论）
        if task.state == "SUCCESS" and isinstance(getattr(task, "result", None), dict):
            outcome = task.result
            payload.update({"ok": bool(outcome.get("ok")), "detail": str(outcome.get("detail") or "")})
        return ApiResponse(data=payload, detail=_("Connectivity test submitted"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="test-result")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def test_result(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """连通性探测结果

        按 task_id 回读异步探测结论：PENDING/STARTED 表示仍在执行
        """
        from celery.result import AsyncResult

        self.get_object()  # 走权限与数据域校验（不能用别人的档案读别人的任务）
        task_id = str(request.query_params.get("task_id") or "").strip()
        if not task_id:
            return ApiResponse(code=1001, detail=_("Missing task id"))
        result = AsyncResult(task_id)
        state = result.state
        data: dict[str, Any] = {"task_id": task_id, "state": state}
        if state == "SUCCESS":
            payload = result.result if isinstance(result.result, dict) else {}
            data.update({"ok": bool(payload.get("ok")), "detail": str(payload.get("detail") or "")})
        elif state == "FAILURE":
            data.update({"ok": False, "detail": str(result.result)[:255]})
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="probe")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def probe(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """能力探测

        按序验证 JSON / tool_calls / reasoning（可选 vision）并落 capabilities

        - 请求体可选 ``capabilities``（能力子集）与 ``vision``（是否追加多模态探测）；
        - 探测不阻断：单项失败只记录 ok=False + 可读原因，返回结果供前端提示与人工修正；
        - 结果写回档案（capabilities + probed_at），可 PATCH 手工覆盖。
        """
        from django.utils import timezone

        from ai.services.assistant.ai_probe import ALL_CAPABILITIES, probe_profile

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
