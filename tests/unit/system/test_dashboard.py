# -*- coding: utf-8 -*-
"""面板统计测试（PERF-02）。

覆盖两处历史 bug 的回归断言：
1. ``percent`` 三元表达式优先级错误导致"较昨日增长"一直返回昨日计数原值；
2. 活跃用户数使用 ``values('last_login').annotate(...).count()``，
   统计的是不同 last_login 值的个数，同一秒登录的多个用户被合并。

同时覆盖：操作日志卡片改为窗口内计数（避免全表 COUNT）与面板短缓存。
"""
import datetime
import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from system.models import OperationLog, UserInfo, UserLoginLog
from system.views.dashboard import trend_info

pytestmark = pytest.mark.django_db

DASHBOARD_URL = "/api/system/dashboard"


def business_queries(ctx):
    """过滤掉 ATOMIC_REQUESTS 产生的 SAVEPOINT / RELEASE。"""
    return [q for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


def payload(response):
    """兼容两种响应：首次为 DRF Response（带 .data），命中缓存后为 HttpResponse。"""
    if hasattr(response, "data"):
        return response.data
    return json.loads(response.content.decode())


def _make_login_log(user, days_ago):
    obj = UserLoginLog.objects.create(creator=user, ipaddress="127.0.0.1")
    UserLoginLog.objects.filter(pk=obj.pk).update(
        created_time=timezone.now() - datetime.timedelta(days=days_ago)
    )
    return obj


def _make_operation_log(days_ago):
    obj = OperationLog.objects.create(module="test", method="GET", path="/api/demo")
    OperationLog.objects.filter(pk=obj.pk).update(
        created_time=timezone.now() - datetime.timedelta(days=days_ago)
    )
    return obj


class TestTrendInfoPercent:
    def test_percent_is_growth_rate(self, superuser):
        """昨日 10 条、今日 15 条 -> 环比增长 50%"""
        for _ in range(10):
            _make_login_log(superuser, days_ago=1)
        for _ in range(15):
            _make_login_log(superuser, days_ago=0)

        results, percent, count = trend_info(UserLoginLog.objects.all(), 7)

        assert results[-1]["count"] == 15
        assert results[-2]["count"] == 10
        assert percent == 50.0
        assert count == 25

    def test_percent_when_no_data_yesterday(self, superuser):
        """昨日 0 条、今日 3 条 -> 100%（旧实现返回 300）"""
        for _ in range(3):
            _make_login_log(superuser, days_ago=0)

        _, percent, _ = trend_info(UserLoginLog.objects.all(), 7)
        assert percent == 100.0

    def test_percent_when_both_zero(self):
        _, percent, _ = trend_info(UserLoginLog.objects.all(), 7)
        assert percent == 0.0

    def test_percent_negative(self, superuser):
        """昨日 20 条、今日 5 条 -> -75%"""
        for _ in range(20):
            _make_login_log(superuser, days_ago=1)
        for _ in range(5):
            _make_login_log(superuser, days_ago=0)

        _, percent, _ = trend_info(UserLoginLog.objects.all(), 7)
        assert percent == -75.0


class TestTrendInfoCountScope:
    def test_window_count_excludes_older_rows(self):
        _make_operation_log(days_ago=0)
        _make_operation_log(days_ago=100)  # 窗口外

        _, _, window_count = trend_info(OperationLog.objects.all(), 7, total_count=False)
        _, _, total_count = trend_info(OperationLog.objects.all(), 7, total_count=True)

        assert window_count == 1
        assert total_count == 2


class TestUserActive:
    def test_active_user_count_counts_users_not_timestamps(self, auth_client):
        """两个用户在同一秒登录 -> 活跃数为 2（旧实现返回 1）"""
        same_time = timezone.now()
        for i in range(2):
            user = UserInfo.objects.create_user(username=f"active{i}", password="Test@123456")
            UserInfo.objects.filter(pk=user.pk).update(last_login=same_time, date_joined=same_time)

        response = auth_client.get(f"{DASHBOARD_URL}/user-active")

        assert response.status_code == 200
        # [ [天数, 注册数, 活跃数], ... ]，第 0 项为"今日"；
        # 旧实现统计不同 last_login 值的个数，两人同秒登录只会被算成 1
        assert payload(response)["data"][0][2] == 2


class TestDashboardCache:
    def test_second_call_served_from_cache(self, auth_client):
        url = f"{DASHBOARD_URL}/user-total"
        with CaptureQueriesContext(connection) as ctx_first:
            first = auth_client.get(url)
        with CaptureQueriesContext(connection) as ctx_second:
            second = auth_client.get(url)

        assert first.status_code == 200
        assert second.status_code == 200
        # 只比较业务数据：响应外壳的 requestId/timestamp 每次请求必然不同
        first_data, second_data = payload(first), payload(second)
        assert second_data["results"] == first_data["results"]
        assert second_data["count"] == first_data["count"]
        assert second_data["percent"] == first_data["percent"]
        assert len(business_queries(ctx_first)) > 0
        # 命中缓存后不再产生业务数据库查询
        assert len(business_queries(ctx_second)) == 0

    def test_today_operate_total_uses_window_count(self, auth_client):
        """窗口外的操作日志不计入 count，避免 OperationLog 全表 COUNT"""
        _make_operation_log(days_ago=0)
        _make_operation_log(days_ago=100)

        response = auth_client.get(f"{DASHBOARD_URL}/today-operate-total")
        data = payload(response)

        assert response.status_code == 200
        assert data["results"][-1]["count"] == 1
        assert data["count"] == 1

    def test_user_login_trend_smoke(self, auth_client, superuser):
        _make_login_log(superuser, days_ago=0)
        response = auth_client.get(f"{DASHBOARD_URL}/user-login-trend")
        assert response.status_code == 200
        assert sum(item["count"] for item in payload(response)["data"]) == 1

    def test_user_registered_trend_smoke(self, auth_client):
        response = auth_client.get(f"{DASHBOARD_URL}/user-registered-trend")
        assert response.status_code == 200
        assert len(payload(response)["data"]) == 31
