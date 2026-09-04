# -*- coding: utf-8 -*-
"""common/core/filter.py 数据权限过滤单元测试（以 demo.Book 模型为载体）。"""
import pytest

from common.core.filter import get_filter_queryset
from demo.models import Book
from system.models import DataPermission

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
    """b1/b2 归 superuser，b3 归 normal_user（admin 字段判定归属）。"""

    def _make(name, isbn, owner):
        return Book.objects.create(
            name=name, isbn=isbn, author=isbn, admin=owner, admin2=owner, file=upload_file
        )

    b1 = _make("A", "i1", superuser)
    b2 = _make("B", "i2", superuser)
    b3 = _make("C", "i3", normal_user)
    return b1, b2, b3


class TestGetFilterQueryset:
    def test_superuser_sees_all(self, superuser, books):
        assert get_filter_queryset(Book.objects.all(), superuser).count() == 3

    def test_user_without_permission_sees_none(self, normal_user, books):
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_owner_rule_filters_own_records(self, normal_user, books):
        normal_user.rules.add(make_permission("own", [make_rule("admin", "value.user.id")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert list(qs) == [books[2]]

    def test_all_rule_grants_everything_in_or_mode(self, normal_user, books):
        normal_user.rules.add(make_permission("all", [make_rule("admin", "value.all", value="*")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3

    def test_all_rule_ignored_in_and_mode(self, normal_user, books):
        """且模式下 ALL 规则被忽略，仅保留其他规则。"""
        rules = [make_rule("admin", "value.all", value="*"), make_rule("admin", "value.user.id")]
        normal_user.rules.add(make_permission("mix", rules, mode=DataPermission.ModeChoices.AND))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert list(qs) == [books[2]]

    def test_owner_department_rule(self, dept, normal_user, books, upload_file):
        """OWNER_DEPARTMENT 规则按用户所属部门过滤（通过 admin__dept 跨表字段）。"""
        b4 = Book.objects.create(
            name="D", isbn="i4", author="a4", admin=normal_user, admin2=normal_user, file=upload_file
        )
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        normal_user.rules.add(
            make_permission("dept-own", [make_rule("admin__dept", "value.user.dept.id")])
        )
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        # b3 的 owner 也是该部门成员，一并可见
        assert list(qs) == [books[2], b4]

    def test_dept_grant_applies_to_dept_members(self, dept, normal_user, superuser, books, upload_file):
        """授权绑定到部门时，部门内成员均生效（个人无单独授权）。"""
        b4 = Book.objects.create(
            name="D", isbn="i4", author="a4", admin=normal_user, admin2=normal_user, file=upload_file
        )
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(make_permission("dept-perm", [make_rule("admin", "value.user.id")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        # b3 的 admin 也是 normal_user 本人
        assert list(qs) == [books[2], b4]

    def test_personal_and_dept_rules_combined_in_or_mode(self, dept, normal_user, superuser, books):
        """个人规则与部门规则在或模式下取并集。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(make_permission("dept-all", [make_rule("admin", "value.all", value="*")]))
        normal_user.rules.add(make_permission("own", [make_rule("admin", "value.user.id")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3

    def test_rule_for_other_table_ignored(self, normal_user, books):
        rules = [make_rule("admin", "value.all", value="*", table="other.model")]
        normal_user.rules.add(make_permission("wrong-table", rules))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_data_permission_disabled_returns_queryset_unchanged(self, normal_user, books, settings):
        settings.PERMISSION_DATA_ENABLED = False
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert qs.count() == 3