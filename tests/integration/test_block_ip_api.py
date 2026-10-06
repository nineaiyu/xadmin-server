# -*- coding: utf-8 -*-
"""IP 拦截名单接口：列表与批量解除拦截（数据源为 Redis 键列表，非 ORM queryset）。

框架批量删除的非逐行分支直接调 ``queryset.delete()``——列表没有该方法，且解除
拦截的副作用只在 perform_destroy 中；本组用例守住「批量删除逐条解除拦截」的行为。
列表枚举按拦截键前缀收集：走 SCAN 游标而非 KEYS 全库扫描（避免阻塞 Redis），
且同域计数键、其他域键等无关键不混入结果；封禁/解封写路径与列表可见性联动不变。
"""

import pytest
from django.core.cache import caches
from django.utils import timezone

from settings.utils.security import LoginBlockUtil, LoginIpBlockUtil, MFABlockUtils
from settings.views.block_ip import IpUtils

pytestmark = pytest.mark.django_db

BLOCK_URL = "/api/settings/ip/block"


def _block(ip):
    """写入拦截键（绕过登录失败计数，直接构造已拦截态），返回列表行的 pk。"""
    caches["default"].set(LoginIpBlockUtil.BLOCK_KEY_TMPL.format(ip), timezone.now().isoformat(), 3600)
    return IpUtils(ip).ip_to_int()


def _blocked(ip):
    return caches["default"].get(LoginIpBlockUtil.BLOCK_KEY_TMPL.format(ip)) is not None


def _listed_ips(auth_client):
    resp = auth_client.get(BLOCK_URL)
    assert resp.status_code == 200, resp.data
    return {row["ip"] for row in resp.data["data"]["results"]}


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


def test_list_collects_only_blocked_prefix(auth_client):
    """列表只收集拦截键：同域计数键/用户级拦截键、其他域键与任意键都不混入。"""
    _block("10.2.0.1")
    _block("10.2.0.2")
    caches["default"].set(LoginBlockUtil.BLOCK_KEY_TMPL.format("alice"), True, 3600)
    caches["default"].set(LoginBlockUtil.LIMIT_KEY_TMPL.format("alice", "10.2.0.1"), 1, 3600)
    caches["default"].set(MFABlockUtils.BLOCK_KEY_TMPL.format("bob"), True, 3600)
    caches["default"].set("unrelated:demo", "x", 3600)

    assert _listed_ips(auth_client) == {"10.2.0.1", "10.2.0.2"}


def test_list_returns_empty_without_blocked_keys(auth_client):
    assert _listed_ips(auth_client) == set()


def test_block_and_unblock_write_path_reflects_in_list(auth_client, settings):
    """真实写路径联动：计数达标即出现在列表，解除拦截后从列表消失。"""
    settings.SECURITY_LOGIN_IP_LIMIT_COUNT = 3
    settings.SECURITY_LOGIN_IP_LIMIT_TIME = 30
    util = LoginIpBlockUtil("10.4.0.1")
    for _ in range(3):
        util.set_block_if_need()
    assert util.is_block() is True
    assert "10.4.0.1" in _listed_ips(auth_client)

    util.clean_block_if_need()
    assert util.is_block() is False
    assert "10.4.0.1" not in _listed_ips(auth_client)
