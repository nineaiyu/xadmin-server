"""「我的管辖」独立权限点回归。

`GET /api/identity/dept/managed` 过去只能靠 `retrieve:SystemDept` 的详情正则兜底，
授权面板里无法单独授予。本用例锁定：只授 `managed:SystemDept` 即可访问；
未授予时 403（而不是页面显示「暂无数据」）。
"""

import pytest
from django.core.cache import cache

pytestmark = pytest.mark.django_db

MANAGED_URL = "/api/identity/dept/managed"


def test_managed_denied_without_permission(api_client, normal_user):
    cache.clear()
    api_client.force_authenticate(user=normal_user)
    resp = api_client.get(MANAGED_URL)
    assert resp.status_code == 403


def test_managed_granted_by_dedicated_permission_point(api_client, normal_user, role, menu_factory):
    role.menu.add(menu_factory("managed:SystemDept", path="api/identity/dept/managed$", method="GET"))
    cache.clear()
    api_client.force_authenticate(user=normal_user)

    resp = api_client.get(MANAGED_URL)

    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000
    assert resp.data["data"]["depts"] == []
    assert resp.data["data"]["direct_count"] == 0
    assert resp.data["data"]["sub_count"] == 0
