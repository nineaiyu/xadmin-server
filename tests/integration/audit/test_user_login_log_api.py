# -*- coding: utf-8 -*-
"""个人安全日志（``/api/audit/user/log``）接口：可达性 + 本人可见域 + 搜索元数据 + 筛选。

回归背景：identity 与 audit 同挂 ``/api/system/`` 前缀时，identity 的
``user/<pk>`` detail 路由注册在前、audit 的静态 ``user/log`` 在后——
``/api/system/user/log`` 永远被前者吞掉（pk 被当成 "log"），安全日志接口实际
不可达且无测试察觉（页面静默空列表）。四域独立前缀后修复。本组用例固定：

1. 端点可达 + 菜单权限门（无 ``list:UserLoginLog`` 的普通用户 403）；
2. 可见域 = 本人记录（``get_queryset`` 的 creator 过滤不可被筛选绕过）；
3. 搜索区元数据（search-fields 基于 filterset 生成——缺 filterset 时页面只有
   搜索按钮而没有搜索框）；
4. 各筛选字段真实生效。

注：请求方用超管身份（跳过行级数据权限/字段权限的额外收敛），聚焦本端点的
creator 可见域与筛选语义；普通用户口径的权限门由独立用例覆盖。
"""

import pytest
from rest_framework.test import APIClient

from audit.models.log import UserLoginLog
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

LIST_URL = "/api/audit/user/log"
FILTER_KEYS = {"status", "login_type", "ipaddress", "city", "browser", "system"}


def _make_log(user, **kwargs):
    defaults = dict(status=True, ipaddress="127.0.0.1", creator=user, login_type=UserLoginLog.LoginTypeChoices.USERNAME)
    defaults.update(kwargs)
    return UserLoginLog.objects.create(**defaults)


def _client_for(user):
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


def _rows(resp):
    return resp.data["data"]["results"]


class TestUserLoginLogEndpoint:
    def test_requires_authentication(self):
        assert APIClient().get(LIST_URL).status_code in (401, 403)

    def test_requires_menu_permission(self, normal_user):
        """无 list:UserLoginLog 权限点的普通用户被权限门拦下（既有 403 口径）。"""
        resp = _client_for(normal_user).get(LIST_URL)

        assert resp.status_code == 403

    def test_list_returns_only_own_records(self, superuser):
        other = UserInfo.objects.create_user(username="log_other_user", password="Test@123456")
        _make_log(superuser, ipaddress="10.0.0.8")
        _make_log(other, ipaddress="10.0.0.9")

        resp = _client_for(superuser).get(LIST_URL)

        assert resp.status_code == 200
        assert [row["ipaddress"] for row in _rows(resp)] == ["10.0.0.8"]

    def test_search_columns_endpoint_available(self, superuser):
        resp = _client_for(superuser).get(f"{LIST_URL}/search-columns")

        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert len(resp.data["data"]) > 0

    def test_search_fields_expose_filter_metadata(self, superuser):
        """搜索区字段元数据：filterset 字段 + ordering 选择器（页面搜索框的数据源）。"""
        resp = _client_for(superuser).get(f"{LIST_URL}/search-fields")

        assert resp.status_code == 200
        items = {item["key"]: item for item in resp.data["data"]}
        assert FILTER_KEYS <= set(items)
        assert items["ordering"]["input_type"] == "select-ordering"
        # 登录类型是枚举：下发 choices 供前端渲染下拉
        assert items["login_type"]["choices"]


class TestUserLoginLogFilters:
    def test_filter_by_status(self, superuser):
        _make_log(superuser, status=True, ipaddress="10.1.0.1")
        _make_log(superuser, status=False, ipaddress="10.1.0.2")
        client = _client_for(superuser)

        failed = client.get(LIST_URL, {"status": "false"})

        assert [row["ipaddress"] for row in _rows(failed)] == ["10.1.0.2"]

    def test_filter_by_login_type(self, superuser):
        _make_log(superuser, login_type=UserLoginLog.LoginTypeChoices.USERNAME, ipaddress="10.2.0.1")
        _make_log(superuser, login_type=UserLoginLog.LoginTypeChoices.SMS, ipaddress="10.2.0.2")

        resp = _client_for(superuser).get(LIST_URL, {"login_type": UserLoginLog.LoginTypeChoices.SMS})

        assert [row["ipaddress"] for row in _rows(resp)] == ["10.2.0.2"]

    def test_filter_ipaddress_icontains(self, superuser):
        _make_log(superuser, ipaddress="10.3.0.1")
        _make_log(superuser, ipaddress="192.168.0.1")

        resp = _client_for(superuser).get(LIST_URL, {"ipaddress": "10.3"})

        assert [row["ipaddress"] for row in _rows(resp)] == ["10.3.0.1"]

    def test_filters_cannot_leak_other_users(self, superuser):
        """筛选参数不能绕过本人可见域：他人日志即使命中筛选也不返回。"""
        other = UserInfo.objects.create_user(username="log_leak_probe", password="Test@123456")
        _make_log(other, ipaddress="10.9.9.9")

        resp = _client_for(superuser).get(LIST_URL, {"ipaddress": "10.9"})

        assert resp.status_code == 200
        assert _rows(resp) == []
