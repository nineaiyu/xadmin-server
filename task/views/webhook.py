#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 订阅与投递审计视图。

- SubscriptionViewSet：CRUD + `events`（事件目录）+ `test`（发送 ping 测试事件，
  走真实投递管线做配置自检）；
- DeliveryViewSet：审计列表（只读）+ `retry`（对 exhausted 投递重置重派）。

订阅 secret 永不回传；管理类资源按菜单权限点控制，无个人/共享分档。
"""

import secrets
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseModelSet,
    BaseViewSet,
    DetailAction,
    ListAction,
    SearchColumnsAction,
    SearchFieldsAction,
)
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from task.models.webhook import WebhookDelivery, WebhookSubscription
from task.serializers.webhook import WebhookDeliverySerializer, WebhookSubscriptionSerializer
from task.services.webhook import encrypt_secret, event_catalog_payload, send_test_delivery

logger = get_logger(__name__)


class SubscriptionFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = WebhookSubscription
        fields = ["is_active"]


class DeliveryFilter(BaseFilterSet):
    class Meta:
        model = WebhookDelivery
        fields = ["status", "event", "subscription"]


class WebhookSubscriptionViewSet(BaseModelSet):
    """Webhook 订阅"""

    queryset = WebhookSubscription.objects.all()
    serializer_class = WebhookSubscriptionSerializer
    ordering = ["-created_time"]
    filterset_class = SubscriptionFilter
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="events")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def events(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """事件目录（key + 中文名 + 契约版本）。"""
        return ApiResponse(data=event_catalog_payload())

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="rotate-secret")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rotate_secret(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """轮换订阅密钥

        生成新密钥并以值级加密落库；明文仅在本次响应返回一次，供订阅方更新配置。
        """
        subscription = self.get_object()
        plaintext = secrets.token_urlsafe(32)
        subscription.secret = encrypt_secret(plaintext)
        subscription.save(update_fields=["secret", "updated_time"])
        return ApiResponse(data={"secret": plaintext})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="test")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def test(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """发送 ping 测试事件

        只对当前订阅建一条投递（不广播给其他订阅者），走真实投递管线做配置自检；
        未启用 / URL 不可投递 / secret 无法解密时直接返回可读原因。
        """
        subscription = self.get_object()
        dispatched, detail = send_test_delivery(subscription)
        if dispatched:
            return ApiResponse(detail=detail)
        return ApiResponse(code=1001, detail=detail)


class WebhookDeliveryViewSet(
    BaseViewSet,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    GenericViewSet,
):
    """投递审计（只读列表 + retry）

    审计记录不提供删除入口，也不注册 destroy / batch-destroy 路由：投递记录是
    「谁在什么时候收到了什么」的证据，一旦可删就失去审计意义，清理统一走保留策略。
    """

    queryset = WebhookDelivery.objects.select_related("subscription")
    serializer_class = WebhookDeliverySerializer
    ordering = ["-created_time"]
    filterset_class = DeliveryFilter
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="retry")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def retry(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """重置投递"""
        delivery = self.get_object()
        if delivery.status not in ("exhausted", "failed"):
            return ApiResponse(code=1001, detail=_("Only failed or exhausted deliveries can be retried"))
        from task.webhook_tasks import dispatch_deliver_webhook

        with transaction.atomic():
            # 行锁内复核状态（并发下可能已被其他请求或到期的旧任务改写）
            locked = (
                WebhookDelivery.objects.select_for_update()
                .filter(pk=delivery.pk, status__in=("exhausted", "failed"))
                .first()
            )
            if locked is None:
                return ApiResponse(code=1001, detail=_("Only failed or exhausted deliveries can be retried"))
            WebhookDelivery.objects.filter(pk=locked.pk).update(
                status="pending", attempt=0, next_retry_at=None, updated_time=timezone.now()
            )
            # 重置与代际递增同事务：队列中残留的旧倒计时任务到期后因代际不匹配静默失效
            dispatch_deliver_webhook(str(locked.pk))
        return ApiResponse(detail=_("Delivery re-dispatched"))
