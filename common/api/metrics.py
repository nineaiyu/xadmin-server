#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Prometheus 指标抓取端点（默认关闭）。"""

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView

from common.metrics import metrics_available, render_metrics


class MetricsAPIView(APIView):
    """``GET /api/common/api/metrics``：Prometheus 指标。

    启用条件（缺一不可，避免匿名暴露系统内部指标）：
    1. ``METRICS_ENABLED=true``：未启用时返回 404（不暴露端点存在性）；
    2. ``METRICS_TOKEN`` 已配置：未配置时返回 403；
    3. 请求携带 ``Authorization: Bearer <METRICS_TOKEN>``。

    端点自身免认证（由令牌校验替代），不参与业务权限链。
    """

    authentication_classes = []
    permission_classes = []

    @extend_schema(exclude=True)
    def get(self, request):
        if not getattr(settings, "METRICS_ENABLED", False):
            return JsonResponse({"detail": "Not found"}, status=404)
        token = getattr(settings, "METRICS_TOKEN", "")
        if not token:
            return JsonResponse({"detail": "METRICS_TOKEN is not configured"}, status=403)
        if request.META.get("HTTP_AUTHORIZATION", "") != f"Bearer {token}":
            return JsonResponse({"detail": "Forbidden"}, status=403)
        if not metrics_available():
            return JsonResponse({"detail": "prometheus-client is not installed"}, status=503)
        payload, content_type = render_metrics()
        return HttpResponse(payload, content_type=content_type)
