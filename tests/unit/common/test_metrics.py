# -*- coding: utf-8 -*-
"""Prometheus 指标端点：默认关闭、令牌保护语义。"""

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

URL = "/api/common/api/metrics"


def test_metrics_disabled_returns_404(api_client):
    """默认关闭：返回 404，不暴露端点存在性。"""
    assert api_client.get(URL).status_code == 404


@override_settings(METRICS_ENABLED=True, METRICS_TOKEN="")
def test_metrics_enabled_without_token_returns_403(api_client):
    """启用但未配置令牌：拒绝访问（否则等同于匿名暴露指标）。"""
    assert api_client.get(URL).status_code == 403


@override_settings(METRICS_ENABLED=True, METRICS_TOKEN="s3cret")
def test_metrics_enabled_requires_bearer_token(api_client):
    """启用且配置令牌：缺失/错误令牌 403，正确令牌 200 且返回指标文本。"""
    assert api_client.get(URL).status_code == 403
    assert api_client.get(URL, HTTP_AUTHORIZATION="Bearer wrong").status_code == 403

    response = api_client.get(URL, HTTP_AUTHORIZATION="Bearer s3cret")
    assert response.status_code == 200
    assert b"xadmin_http_requests_total" in response.content
