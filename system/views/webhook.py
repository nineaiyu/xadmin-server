#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Webhook 订阅与投递审计视图（ADR-022）。

- SubscriptionViewSet：CRUD + `events`（事件目录）+ `test`（发送 ping 测试事件，
  走真实投递管线做配置自检）；
- DeliveryViewSet：审计列表（只读）+ `retry`（对 exhausted 投递重置重派）。

订阅 secret 永不回传；管理类资源按菜单权限点控制，无个人/共享分档。
"""

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ListDeleteModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from django_filters import rest_framework as filters
from system.models.webhook import WebhookDelivery, WebhookSubscription
from system.serializers.webhook import WebhookDeliverySerializer, WebhookSubscriptionSerializer
from system.utils.webhook import EVENT_CATALOG, get_event_label

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
    @action(methods=["get"], detail=False, url_path="events")
    def events(self, request, *args, **kwargs):
        """事件目录（key + 中文名）。"""
        return ApiResponse(data=[{"key": key, "label": get_event_label(key)} for key in EVENT_CATALOG])

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="test")
    def test(self, request, *args, **kwargs):
        """发送 ping 测试事件（走真实投递管线，配置自检）。"""
        subscription = self.get_object()
        from system.utils.webhook import emit_webhook_event

        count = emit_webhook_event("webhook.ping", {"subscription": subscription.name})
        if count:
            return ApiResponse(detail=_("Test event dispatched, check the delivery audit page"))
        return ApiResponse(
            code=1001, detail=_("Test event was not dispatched (subscription inactive or event not subscribed)")
        )


class WebhookDeliveryViewSet(ListDeleteModelSet):
    """投递审计（只读列表 + retry）"""

    queryset = WebhookDelivery.objects.select_related("subscription")
    serializer_class = WebhookDeliverySerializer
    ordering = ["-created_time"]
    filterset_class = DeliveryFilter
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="retry")
    def retry(self, request, *args, **kwargs):
        """重置 exhausted/failed 投递并立即重派。"""
        delivery = self.get_object()
        if delivery.status not in ("exhausted", "failed"):
            return ApiResponse(code=1001, detail=_("Only failed or exhausted deliveries can be retried"))
        delivery.status = "pending"
        delivery.attempt = 0
        delivery.next_retry_at = None
        delivery.save(update_fields=["status", "attempt", "next_retry_at", "updated_time"])
        from system.webhook_tasks import deliver_webhook

        deliver_webhook.apply_async(kwargs={"delivery_id": str(delivery.pk)}, task_id=str(delivery.pk))
        return ApiResponse(detail=_("Delivery re-dispatched"))
