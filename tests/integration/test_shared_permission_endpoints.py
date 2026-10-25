# -*- coding: utf-8 -*-
"""同一 path+method 登记多个权限码的端点：任一单边授权都应可访问。

覆盖设置 basic（GET）、操作日志（GET）、任务执行（GET）；主权限点选择与字段权限
并集由 ``tests/unit/common/test_core_permission.py`` 守护。
"""

import pytest
from django.core.cache import cache as django_cache

pytestmark = pytest.mark.django_db

SHARED_ENDPOINTS = [
    ("api/settings/basic$", "retrieve:SettingBasic", "/api/settings/basic"),
    ("api/settings/basic$", "retrieve:SettingWatermark", "/api/settings/basic"),
    ("api/audit/logs/operation$", "list:SystemOperationLog", "/api/audit/logs/operation"),
    ("api/audit/logs/operation$", "changeHistory:SystemUser", "/api/audit/logs/operation"),
    ("api/audit/logs/operation$", "changeHistory:SystemDept", "/api/audit/logs/operation"),
    ("api/task/executions$", "list:SystemTaskExecution", "/api/task/executions"),
    ("api/task/executions$", "log:SystemTask", "/api/task/executions"),
]


@pytest.mark.parametrize("menu_path,code,endpoint", SHARED_ENDPOINTS)
def test_single_side_code_can_access(api_client, normal_user, role, menu_factory, menu_path, code, endpoint):
    role.menu.add(menu_factory(code, path=menu_path, method="GET"))
    django_cache.clear()

    api_client.force_authenticate(user=normal_user)
    resp = api_client.get(endpoint)
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data


def test_settings_read_keeps_or_semantics(api_client, normal_user, role, menu_factory):
    """读取保持 OR 语义（产品决策）：水印权限可读全部设置字段，仅写入做字段隔离。

    设置项不是敏感值，读取隔离会让元数据/搜索列缓存按角色分片，收益低于复杂度；
    该决策由本用例钉住，若未来改为读隔离需同步改前端设置页与缓存键。
    """
    role.menu.add(menu_factory("retrieve:SettingWatermark", path="api/settings/basic$", method="GET"))
    django_cache.clear()

    api_client.force_authenticate(user=normal_user)
    resp = api_client.get("/api/settings/basic")
    assert resp.data["code"] == 1000, resp.data
    data = resp.data["data"]
    assert "SITE_URL" in data
    assert "PERMISSION_FIELD_ENABLED" in data
    assert "FRONT_END_WEB_WATERMARK_TEXT" in data
