# -*- coding: utf-8 -*-
"""PERF-09：数据权限过滤查询收敛测试。

覆盖：
1. 部门树递归结果缓存：SQL 数不随调用次数增长，部门变更后自动失效；
2. 数据权限过滤的 SQL 数在部门树深度增加时保持常数；
3. 个人授权判断改用 exists()（无 COUNT 聚合查询）；
4. 数据权限行为不因缓存而改变。
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.filter import get_filter_queryset
from demo.models import Book
from system.models import DataPermission, DeptInfo

pytestmark = pytest.mark.django_db


def make_rule(field, type_, value="*", match="exact", table="demo.book"):
    return {"table": table, "field": field, "type": type_, "value": value, "match": match}


def make_permission(name, rules, mode=DataPermission.ModeChoices.OR):
    return DataPermission.objects.create(name=name, rules=rules, mode_type=mode)


@pytest.fixture
def upload_file(superuser):
    from system.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


@pytest.fixture
def books(superuser, normal_user, upload_file):
    def _make(name, isbn, owner):
        return Book.objects.create(
            name=name, isbn=isbn, author=isbn, admin=owner, admin2=owner, file=upload_file
        )

    return [_make("A", "i1", superuser), _make("B", "i2", superuser), _make("C", "i3", normal_user)]


@pytest.fixture(autouse=True)
def _clear_dept_tree_cache():
    cache.delete_pattern("dept_recursion_*")
    yield
    cache.delete_pattern("dept_recursion_*")


def _business_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


def _make_dept_chain(depth, code_prefix):
    """构造一条 depth 层的部门链：root -> child1 -> ... -> leaf，返回叶子节点"""
    parent = None
    for i in range(depth):
        parent = DeptInfo.objects.create(name=f"{code_prefix}-{i}", code=f"{code_prefix}{i}", parent=parent)
    return parent


class TestDeptTreeCache:
    def test_recursion_result_is_cached(self, db):
        leaf = _make_dept_chain(4, "a")

        with CaptureQueriesContext(connection) as first_ctx:
            first = DeptInfo.recursion_dept_info(leaf.pk, is_parent=True)
        with CaptureQueriesContext(connection) as second_ctx:
            second = DeptInfo.recursion_dept_info(leaf.pk, is_parent=True)

        assert len(first) == 4
        assert sorted(first) == sorted(second)
        # 第二次命中缓存，不再查询部门表
        assert len(_business_queries(first_ctx)) > 0
        assert len(_business_queries(second_ctx)) == 0

    def test_cache_invalidated_on_dept_change(self, db):
        leaf = _make_dept_chain(3, "b")
        assert len(DeptInfo.recursion_dept_info(leaf.pk, is_parent=True)) == 3

        # 新增下级部门 -> 信号失效缓存 -> 下行递归结果应包含新部门
        child = DeptInfo.objects.create(name="new", code="bnew", parent=leaf)
        result = [str(pk) for pk in DeptInfo.recursion_dept_info(leaf.pk)]
        assert str(child.pk) in result

    def test_child_direction_recursion(self, db):
        leaf = _make_dept_chain(3, "c")
        child_of_leaf = DeptInfo.objects.create(name="leaf-child", code="clc", parent=leaf)
        result = [str(pk) for pk in DeptInfo.recursion_dept_info(leaf.pk)]
        assert str(child_of_leaf.pk) in result
        assert str(leaf.pk) in result

    def test_parent_direction_recursion(self, db):
        leaf = _make_dept_chain(3, "d")
        result = [str(pk) for pk in DeptInfo.recursion_dept_info(leaf.pk, is_parent=True)]
        assert len(result) == 3


class TestFilterQueryCount:
    def _measure(self, normal_user, depth, code_prefix):
        leaf = _make_dept_chain(depth, code_prefix)
        normal_user.dept = leaf
        normal_user.save(update_fields=["dept"])
        cache.delete_pattern("dept_recursion_*")

        with CaptureQueriesContext(connection) as ctx:
            get_filter_queryset(Book.objects.all(), normal_user)
        return len(_business_queries(ctx))

    def test_query_count_constant_regardless_of_dept_depth(self, normal_user, books):
        """非超管的权限解析查询数不随部门树深度线性增长"""
        counts = {}
        for depth in (1, 3, 5):
            counts[depth] = self._measure(normal_user, depth, f"q{depth}")
        assert counts[1] == counts[3] == counts[5], counts

    def test_no_count_aggregation_for_personal_rules(self, normal_user, books):
        """个人授权存在性判断使用 exists()，权限解析不应出现 COUNT(*) 聚合"""
        normal_user.rules.add(make_permission("own", [make_rule("admin", "value.user.id")]))

        with CaptureQueriesContext(connection) as ctx:
            get_filter_queryset(Book.objects.all(), normal_user)

        for sql in _business_queries(ctx):
            assert "COUNT(" not in sql.upper(), sql


class TestPermissionBehaviorUnchanged:
    def test_dept_grant_still_applies(self, dept, normal_user, books, upload_file):
        """优化后部门授权行为与旧实现一致"""
        b4 = Book.objects.create(
            name="D", isbn="i4", author="a4", admin=normal_user, admin2=normal_user, file=upload_file
        )
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(make_permission("dept-perm", [make_rule("admin", "value.user.id")]))

        result = list(get_filter_queryset(Book.objects.all(), normal_user))
        assert books[2] in result and b4 in result

    def test_no_permission_means_nothing_visible(self, dept, normal_user, books):
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_multi_level_dept_grant(self, normal_user, books, upload_file):
        """多级部门树上每一级都有授权时（部门权限按且模式组合）行为不变"""
        leaf = _make_dept_chain(3, "m")
        normal_user.dept = leaf
        normal_user.save(update_fields=["dept"])
        b4 = Book.objects.create(
            name="D", isbn="i4", author="a4", admin=normal_user, admin2=normal_user, file=upload_file
        )
        for code in ("m0", "m1", "m2"):
            DeptInfo.objects.get(code=code).rules.add(
                make_permission(f"perm-{code}", [make_rule("admin", "value.user.id")]))
        cache.delete_pattern("dept_recursion_*")

        result = list(get_filter_queryset(Book.objects.all(), normal_user))
        assert books[2] in result and b4 in result

    def test_ancestor_only_grant_blocked_by_and_mode(self, normal_user, books, upload_file):
        """部门权限按且模式组合：树上存在无授权部门时整体不放行（与旧实现一致的既有语义）"""
        leaf = _make_dept_chain(2, "n")
        normal_user.dept = leaf
        normal_user.save(update_fields=["dept"])
        DeptInfo.objects.get(code="n0").rules.add(
            make_permission("perm-root", [make_rule("admin", "value.user.id")]))
        cache.delete_pattern("dept_recursion_*")

        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_permission_shared_across_depts_counted_once_per_dept(self, dept, normal_user, books, upload_file):
        """同一条授权挂在多个部门时，按部门分组后各自生效"""
        b4 = Book.objects.create(
            name="D", isbn="i4", author="a4", admin=normal_user, admin2=normal_user, file=upload_file
        )
        rule = [make_rule("admin", "value.user.id")]
        parent = DeptInfo.objects.create(name="p", code="pp")
        child = DeptInfo.objects.create(name="c", code="cc", parent=parent)
        shared = make_permission("shared", rule)
        parent.rules.add(shared)
        child.rules.add(shared)
        normal_user.dept = child
        normal_user.save(update_fields=["dept"])
        cache.delete_pattern("dept_recursion_*")

        result = list(get_filter_queryset(Book.objects.all(), normal_user))
        assert books[2] in result and b4 in result

    def test_owner_rule_filters_own_records(self, normal_user, books):
        normal_user.rules.add(make_permission("own", [make_rule("admin", "value.user.id")]))
        assert list(get_filter_queryset(Book.objects.all(), normal_user)) == [books[2]]

    def test_all_rule_grants_everything_in_or_mode(self, normal_user, books):
        normal_user.rules.add(make_permission("all", [make_rule("admin", "value.all", value="*")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3
