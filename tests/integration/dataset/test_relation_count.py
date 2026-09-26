# -*- coding: utf-8 -*-
"""列表关联计数声明式集成测试。

口径钉死：

- 序列化器声明 ``relation_count_fields``（**注解名 = 字段名**），视图混入 ``RelationCountMixin``
  后列表 / 详情 / 导出把 ``Count`` 并入主查询（逐行 COUNT 归零，N+1 消除）；
- 写操作不注解（无 GROUP BY 污染）；未声明 / 未混入的视图零变化；
- ``annotate`` 后显式补回 ``Meta.ordering``（分页顺序稳定）；
- 计数与影响面同源（角色→用户、数据集→报表）。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from dataset.models.dataset import Dataset, Report
from system.models import UserInfo, UserRole
from system.views.admin.role import RoleViewSet

pytestmark = pytest.mark.django_db

ROLE_URL = "/api/system/role"
DATASET_URL = "/api/system/datasets"
USER_URL = "/api/system/user"


def _results(resp):
    assert resp.status_code == 200
    return resp.json()["data"]["results"]


def _per_row_count_queries(ctx, table):
    """筛出逐行 COUNT：命中关联表且无 GROUP BY（主查询 annotate 带 GROUP BY 会排除）。"""
    return [
        q
        for q in ctx.captured_queries
        if "COUNT(*)" in q["sql"].upper() and table in q["sql"] and "GROUP BY" not in q["sql"].upper()
    ]


@pytest.fixture
def role_page(db):
    """3 个角色：2 用户 / 1 用户 / 0 用户。"""
    counts = [2, 1, 0]
    roles = []
    for index, count in enumerate(counts):
        role = UserRole.objects.create(name=f"计数角色{index}", code=f"count_role_{index}")
        for j in range(count):
            user = UserInfo.objects.create_user(username=f"rc_{index}_{j}", password="Test@123456")
            user.roles.add(role)
        roles.append(role)
    return roles


class TestRelationCountDeclarative:
    def test_list_counts_match_and_no_per_row_count(self, auth_client, role_page, monkeypatch):
        # 关闭 mixin（置空白名单）得到逐行 COUNT 基线
        monkeypatch.setattr(RoleViewSet, "auto_prefetch_actions", ())
        with CaptureQueriesContext(connection) as ctx_base:
            base = _results(auth_client.get(ROLE_URL, {"page": 1, "size": 100}))
        monkeypatch.undo()

        with CaptureQueriesContext(connection) as ctx_opt:
            optimized = _results(auth_client.get(ROLE_URL, {"page": 1, "size": 100}))

        base_map = {item["code"]: item["user_count"] for item in base}
        opt_map = {item["code"]: item["user_count"] for item in optimized}
        assert base_map == opt_map  # 优化前后数据一致（含内置角色）
        own = {code: opt_map.get(code) for code in ("count_role_0", "count_role_1", "count_role_2")}
        assert own == {"count_role_0": 2, "count_role_1": 1, "count_role_2": 0}

        # 3 个自建角色：基线每行一次 COUNT，声明式预聚合后逐行 COUNT 归零
        assert len(_per_row_count_queries(ctx_base, "system_userinfo_roles")) >= 3
        assert _per_row_count_queries(ctx_opt, "system_userinfo_roles") == []

    def test_write_action_not_annotated(self, db):
        view = RoleViewSet()
        view.action = "list"
        assert "user_count" in view.get_queryset().query.annotations

        view = RoleViewSet()
        view.action = "destroy"
        assert "user_count" not in view.get_queryset().query.annotations

    def test_annotate_keeps_ordering(self, db):
        view = RoleViewSet()
        view.action = "list"
        sql = str(view.get_queryset().query)
        assert "GROUP BY" in sql
        assert "ORDER BY" in sql

    def test_single_object_falls_back(self, role_page):
        """未走 mixin 的单对象序列化回退为单次 COUNT，结果一致。"""
        from system.serializers.role import RoleSerializer

        plain = UserRole.objects.get(pk=role_page[0].pk)
        assert not hasattr(plain, "user_count")
        assert RoleSerializer().get_user_count(plain) == 2

    def test_undeclared_view_untouched(self, auth_client):
        """未混入 mixin 的视图（用户列表）queryset 无关联计数注解。"""
        from system.views.admin.user import UserViewSet

        view = UserViewSet()
        view.action = "list"
        assert "user_count" not in view.get_queryset().query.annotations


class TestDatasetReportCount:
    def test_list_report_count(self, auth_client):
        dataset_with = Dataset.objects.create(name="计数数据集A", bound_model="system.userinfo")
        Dataset.objects.create(name="计数数据集B", bound_model="system.userinfo")  # 无报表引用 → 计数 0
        for index in range(2):
            Report.objects.create(name=f"计数报表{index}", dataset=dataset_with)

        with CaptureQueriesContext(connection) as ctx:
            rows = _results(auth_client.get(DATASET_URL, {"page": 1, "size": 100}))

        mapping = {item["name"]: item["report_count"] for item in rows}
        assert mapping["计数数据集A"] == 2
        assert mapping["计数数据集B"] == 0
        assert _per_row_count_queries(ctx, "system_report") == []


class TestCrossPageFilter:
    def test_role_filter_for_drilldown(self, auth_client, role_page):
        """角色列表「用户数」跳转链路：用户列表按 role（角色 pk）过滤可用。"""
        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "role": role_page[0].pk})
        usernames = {item["username"] for item in _results(resp)}
        assert usernames == {"rc_0_0", "rc_0_1"}
