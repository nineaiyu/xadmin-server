# -*- coding: utf-8 -*-
"""部门树批量展开（DeptInfo.dept_tree_pks）：
与逐个 `recursion_dept_info` 的并集逐项一致 + 单次全表查询 + 缓存与失效同源。
"""

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from system.models import DeptInfo

pytestmark = pytest.mark.django_db


@pytest.fixture()
def dept_tree():
    """三层部门树：root → child → grandchild，另有独立分支 other。"""
    root = DeptInfo.objects.create(name="总部", code="root")
    child = DeptInfo.objects.create(name="研发", code="child", parent=root)
    grandchild = DeptInfo.objects.create(name="后端", code="grandchild", parent=child)
    other = DeptInfo.objects.create(name="市场", code="other")
    return {"root": root, "child": child, "grandchild": grandchild, "other": other}


def _per_pk_union(pks, is_parent=False):
    merged = []
    for pk in pks:
        merged.extend(DeptInfo.recursion_dept_info(str(pk), is_parent=is_parent))
    return sorted(set(str(item) for item in merged))


class TestParityWithPerPkRecursion:
    def test_subtree_matches_per_pk_union(self, dept_tree):
        pks = [dept_tree["root"].pk, dept_tree["other"].pk]
        batch = [str(item) for item in DeptInfo.dept_tree_pks(pks)]
        assert sorted(batch) == _per_pk_union(pks)
        assert str(dept_tree["grandchild"].pk) in batch, "孙级部门应被展开"

    def test_leaf_and_mid_node_matches(self, dept_tree):
        pks = [dept_tree["child"].pk]
        assert [str(item) for item in DeptInfo.dept_tree_pks(pks)] == _per_pk_union(pks)

    def test_is_parent_direction_matches(self, dept_tree):
        pks = [dept_tree["grandchild"].pk, dept_tree["other"].pk]
        batch = [str(item) for item in DeptInfo.dept_tree_pks(pks, is_parent=True)]
        assert sorted(batch) == _per_pk_union(pks, is_parent=True)
        assert str(dept_tree["root"].pk) in batch, "向上方向应包含祖先"

    def test_unknown_pk_kept_like_single_path(self, dept_tree):
        """未知主键：单条路径会原样返回该 pk，批量路径必须一致（条件不会静默变宽/变窄）。"""
        ghost = "0f0e0d0c-0b0a-0908-0706-050403020100"
        pks = [ghost]
        assert [str(item) for item in DeptInfo.dept_tree_pks(pks)] == _per_pk_union(pks)

    def test_empty_input_returns_empty(self):
        assert DeptInfo.dept_tree_pks([]) == []
        assert DeptInfo.dept_tree_pks(None) == []


class TestQueryAndCache:
    def test_single_query_for_multiple_departments(self, dept_tree):
        cache.clear()
        pks = [dept_tree["root"].pk, dept_tree["child"].pk, dept_tree["other"].pk]
        with CaptureQueriesContext(connection) as captured:
            DeptInfo.dept_tree_pks(pks)
        assert len(captured) == 1, [query["sql"] for query in captured]

    def test_second_call_hits_cache(self, dept_tree):
        cache.clear()
        pks = [dept_tree["root"].pk]
        first = DeptInfo.dept_tree_pks(pks)
        with CaptureQueriesContext(connection) as captured:
            second = DeptInfo.dept_tree_pks(pks)
        assert second == first
        assert len(captured) == 0

    def test_cache_invalidated_on_dept_change(self, dept_tree):
        """部门变更后展开结果立即反映新子节点（缓存键前缀与单条路径同源，共用失效信号）。"""
        pks = [dept_tree["child"].pk]
        before = DeptInfo.dept_tree_pks(pks)
        assert str(dept_tree["grandchild"].pk) in [str(item) for item in before]

        fresh = DeptInfo.objects.create(name="前端", code="fresh", parent=dept_tree["child"])
        DeptInfo.invalid_dept_tree_cache()
        after = [str(item) for item in DeptInfo.dept_tree_pks(pks)]
        assert str(fresh.pk) in after, "变更并失效后应看到新部门"
