# -*- coding: utf-8 -*-
"""BaseViewSet 查询优化测试（自动 select_related / prefetch_related 消除 N+1）。

覆盖：
1. 自动推断：FK -> select_related，M2M -> prefetch_related，仅输出 pk 的字段不处理；
2. optimize_queryset 的 action 白名单、显式声明、开关行为；
3. 列表接口 SQL 数量不随行数线性增长，且优化前后响应数据一致。
"""
import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test.utils import CaptureQueriesContext

from system.models import UserInfo, UserLoginLog
from system.views.admin.loginlog import LoginLogViewSet
from system.views.admin.user import UserViewSet

pytestmark = pytest.mark.django_db

USER_URL = "/api/system/user"
LOGIN_LOG_URL = "/api/system/logs/login"


@pytest.fixture
def user_page(db, dept, role, superuser):
    """一页业务数据：5 个带部门/角色的用户（superuser 也挂上部门/角色，保证每行都有 3 个关联查询），
    前 3 个用户各有 2 条 websocket 登录日志。"""
    superuser.dept = dept
    superuser.save(update_fields=["dept"])
    superuser.roles.add(role)
    users = [superuser]
    for i in range(5):
        user = UserInfo.objects.create(
            username=f"user{i:02d}",
            nickname=f"用户{i:02d}",
            password=make_password("Xadmin@123456"),
            dept=dept,
        )
        user.roles.add(role)
        users.append(user)
        if i < 3:
            for seq in range(2):
                UserLoginLog.objects.create(
                    creator=user,
                    ipaddress="127.0.0.1",
                    channel_name=f"channel-{user.username}-{seq}",
                    login_type=UserLoginLog.LoginTypeChoices.WEBSOCKET,
                )
    return users


class TestSerializerRelatedFieldsInference:
    def test_user_viewset_inference(self):
        view = UserViewSet()
        view.action = "list"
        select_fields, prefetch_fields = view.get_serializer_related_fields()
        assert select_fields == ["dept"]
        assert prefetch_fields == ["roles", "rules"]

    def test_login_log_viewset_inference(self):
        view = LoginLogViewSet()
        view.action = "list"
        select_fields, prefetch_fields = view.get_serializer_related_fields()
        assert select_fields == ["creator"]
        assert prefetch_fields == []

    def test_inference_cached_on_view_instance(self):
        view = UserViewSet()
        view.action = "list"
        assert view.get_serializer_related_fields() == view.get_serializer_related_fields()
        assert view._serializer_related_fields[0] == ["dept"]


class TestOptimizeQuerysetBehavior:
    def test_auto_prefetch_only_on_configured_actions(self):
        view = UserViewSet()
        view.action = "update"
        result = view.optimize_queryset(UserInfo.objects.all())
        assert not result.query.select_related
        assert not result._prefetch_related_lookups

        view.action = "list"
        result = view.optimize_queryset(UserInfo.objects.all())
        assert result.query.select_related
        assert set(result._prefetch_related_lookups) == {"roles", "rules"}

    def test_explicit_fields_apply_on_all_actions(self):
        view = LoginLogViewSet()
        view.action = "update"
        view.select_related_fields = ("creator",)
        result = view.optimize_queryset(UserLoginLog.objects.all())
        assert result.query.select_related

    def test_auto_prefetch_disabled_keeps_explicit_fields(self):
        view = UserViewSet()
        view.action = "list"
        view.auto_prefetch_related = False
        view.prefetch_related_fields = ("roles",)
        result = view.optimize_queryset(UserInfo.objects.all())
        assert set(result._prefetch_related_lookups) == {"roles"}

    def test_non_queryset_passthrough(self):
        view = UserViewSet()
        view.action = "list"
        assert view.optimize_queryset(["not", "a", "queryset"]) == ["not", "a", "queryset"]


class TestListQueryCount:
    def test_user_list_query_count_and_response(self, auth_client, user_page, monkeypatch):
        monkeypatch.setattr(UserViewSet, "auto_prefetch_related", False)
        with CaptureQueriesContext(connection) as ctx_base:
            resp_base = auth_client.get(USER_URL, {"page": 1, "size": 10})
        monkeypatch.undo()

        with CaptureQueriesContext(connection) as ctx_opt:
            resp_opt = auth_client.get(USER_URL, {"page": 1, "size": 10})

        assert resp_base.status_code == 200
        assert resp_opt.status_code == 200
        # 只比较业务数据：响应外壳的 requestId/timestamp 每次请求必然不同
        assert resp_opt.data["data"] == resp_base.data["data"]

        baseline = len(ctx_base.captured_queries)
        optimized = len(ctx_opt.captured_queries)
        # 页内 6 个用户（含 superuser），每个用户 dept/roles/rules 三个关联字段：
        # 基线 18 条逐行查询，优化后被 2 条 M2M 批量查询替代（dept 走 JOIN），净省 16 条
        assert baseline - optimized == 16

    def test_login_log_list_query_count_and_response(self, auth_client, user_page, monkeypatch):
        monkeypatch.setattr(LoginLogViewSet, "auto_prefetch_related", False)
        with CaptureQueriesContext(connection) as ctx_base:
            resp_base = auth_client.get(LOGIN_LOG_URL, {"page": 1, "size": 20})
        monkeypatch.undo()

        with CaptureQueriesContext(connection) as ctx_opt:
            resp_opt = auth_client.get(LOGIN_LOG_URL, {"page": 1, "size": 20})

        assert resp_base.status_code == 200
        assert resp_opt.status_code == 200
        assert resp_opt.data["data"] == resp_base.data["data"]

        baseline = len(ctx_base.captured_queries)
        optimized = len(ctx_opt.captured_queries)
        # 页内 6 条日志，每条基线多一次 creator 查询
        assert baseline - optimized >= 6
