# -*- coding: utf-8 -*-
"""IP 拦截名单接口：列表与批量解除拦截（数据源为 Redis 键列表，非 ORM queryset）。

框架批量删除的非逐行分支直接调 ``queryset.delete()``——列表没有该方法，且解除
拦截的副作用只在 perform_destroy 中；本组用例守住「批量删除逐条解除拦截」的行为。
"""

import pytest
from django.core.cache import caches
from django.utils import timezone

from settings.utils.security import LoginIpBlockUtil
from settings.views.block_ip import IpUtils

pytestmark = pytest.mark.django_db

BLOCK_URL = "/api/settings/ip/block"


def _block(ip):
    """写入拦截键（绕过登录失败计数，直接构造已拦截态），返回列表行的 pk。"""
    caches["default"].set(LoginIpBlockUtil.BLOCK_KEY_TMPL.format(ip), timezone.now().isoformat(), 3600)
    return IpUtils(ip).ip_to_int()


def _blocked(ip):
    return caches["default"].get(LoginIpBlockUtil.BLOCK_KEY_TMPL.format(ip)) is not None


def test_list_shows_blocked_ips(auth_client):
    _block("10.2.0.1")

    resp = auth_client.get(BLOCK_URL)

    assert resp.status_code == 200, resp.data
    assert [row["ip"] for row in resp.data["data"]["results"]] == ["10.2.0.1"]


def test_batch_destroy_unblocks_selected_only(auth_client):
    target = _block("10.2.0.1")
    _block("10.2.0.2")

    resp = auth_client.post(f"{BLOCK_URL}/batch-destroy", [target], format="json")

    assert resp.status_code == 200, resp.data
    assert not _blocked("10.2.0.1")
    assert _blocked("10.2.0.2")


def test_batch_destroy_rejects_non_list_payload(auth_client):
    _block("10.2.0.1")

    resp = auth_client.post(f"{BLOCK_URL}/batch-destroy", {"pk": "1"}, format="json")

    assert resp.data["code"] == 1004
    assert _blocked("10.2.0.1")
