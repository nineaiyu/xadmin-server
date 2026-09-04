# -*- coding: utf-8 -*-
"""部门 user_count 预聚合测试（AnnotateUserCountMixin 消除逐行 COUNT）。

覆盖：
1. annotate 仅在会逐行序列化的 action 生效，写操作不被聚合污染；
2. GROUP BY 下 Django 不再套用 Meta.ordering，mixin 须显式补回排序（回归防护）；
3. 列表接口逐行 COUNT 归零，且优化前后响应数据一致；
4. serializer 在有/无 annotate 两条路径下结果一致。
"""
import pytest
from django.db import connection
from django.db.models import Count
from django.test.utils import CaptureQueriesContext

from system.models import DeptInfo, UserInfo
from system.serializers.department import DeptSerializer
from system.views.admin.dept import DeptViewSet

pytestmark = pytest.mark.django_db

DEPT_URL = "/api/system/dept"


@pytest.fixture
def dept_page(db):
    """5 个部门，每个部门挂 2 个用户，用于观察 user_count 的逐行 COUNT。"""
    depts = []
    for i in range(5):
        dept = DeptInfo.objects.create(name=f"部门{i:02d}", code=f"dept_{i:02d}")
        for j in range(2):
            UserInfo.objects.create_user(username=f"u{i:02d}{j}", password="Test@123456", dept=dept)
        depts.append(dept)
    return depts


def _per_row_count_queries(ctx):
    """筛出 user_count 的逐行 COUNT：只涉及 system_userinfo 的 COUNT(*)。

    排除两类干扰：分页器统计 system_deptinfo 总数的 COUNT，
    以及 annotate 主查询（LEFT JOIN 了 system_userinfo，但同时涉及 system_deptinfo）。
    """
    return [
        q for q in ctx.captured_queries
        if "COUNT(*)" in q["sql"].upper()
        and "system_userinfo" in q["sql"]
        and "system_deptinfo" not in q["sql"]
    ]


class TestAnnotateUserCountMixin:
    def test_annotate_only_on_serialized_actions(self, db):
        view = DeptViewSet()
        view.action = "list"
        assert "user_count" in view.get_queryset().query.annotations

        view = DeptViewSet()
        view.action = "destroy"
        assert "user_count" not in view.get_queryset().query.annotations

    def test_annotate_keeps_model_ordering(self, db):
        """回归：聚合查询下 Django 不套用 Meta.ordering，mixin 必须显式补回，否则分页顺序不稳定。"""
        view = DeptViewSet()
        view.action = "list"
        sql = str(view.get_queryset().query)
        assert "GROUP BY" in sql
        assert "ORDER BY" in sql


class TestDeptUserCount:
    def test_list_removes_per_row_count(self, auth_client, dept_page, monkeypatch):
        # 置空 action 白名单即关闭 annotate，serializer 回退为逐对象 COUNT
        monkeypatch.setattr(DeptViewSet, "auto_prefetch_actions", ())
        with CaptureQueriesContext(connection) as ctx_base:
            resp_base = auth_client.get(DEPT_URL, {"page": 1, "size": 100})
        monkeypatch.undo()

        with CaptureQueriesContext(connection) as ctx_opt:
            resp_opt = auth_client.get(DEPT_URL, {"page": 1, "size": 100})

        assert resp_base.status_code == 200
        assert resp_opt.status_code == 200
        # 只比较业务数据：响应外壳的 requestId/timestamp 每次请求必然不同
        assert resp_opt.data["data"] == resp_base.data["data"]

        # 5 个部门：基线每行一次 COUNT，annotate 后聚合并入主查询，逐行 COUNT 归零
        assert len(_per_row_count_queries(ctx_base)) == 5
        assert _per_row_count_queries(ctx_opt) == []

    def test_serializer_falls_back_without_annotate(self, dept_page):
        """有 annotate 取聚合值，无 annotate 回退单对象聚合，两条路径结果一致。"""
        annotated = DeptInfo.objects.annotate(user_count=Count("dept_query")).get(pk=dept_page[0].pk)
        plain = DeptInfo.objects.get(pk=dept_page[0].pk)
        assert not hasattr(plain, "user_count")

        serializer = DeptSerializer()
        assert serializer.get_user_count(annotated) == 2
        assert serializer.get_user_count(plain) == 2
