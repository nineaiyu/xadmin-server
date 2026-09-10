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
        return Book.objects.create(name=name, isbn=isbn, author=isbn, admin=owner, admin2=owner, file=upload_file)

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
        normal_user.rules.add(make_permission("dept-own", [make_rule("admin__dept", "value.user.dept.id")]))
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

    # ---------- 2026-09 重构：合并语义「取最宽生效」与主管规则 ----------

    def test_ancestor_grant_applies_without_self_grant(self, dept, normal_user, superuser, books):
        """祖先部门有授权、本部门未配：祖先授权直接生效（旧实现逐层 AND 会全灭）。"""
        parent = type(dept).objects.create(name="总公司", code="hq")
        dept.parent = parent
        dept.save(update_fields=["parent"])
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        parent.rules.add(
            make_permission("hq-own", [make_rule("admin", "value.table.user.ids", value='[{"pk": %s}]' % superuser.pk)])
        )
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        # 祖先授权圈 superuser 的书；本部门无授权不收紧
        assert set(qs) == {books[0], books[1]}

    def test_ancestor_and_self_grants_union(self, dept, normal_user, superuser, books):
        """祖先授权与本级授权取并集（旧实现为逐层 AND 交集）。"""
        parent = type(dept).objects.create(name="总公司", code="hq2")
        dept.parent = parent
        dept.save(update_fields=["parent"])
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        parent.rules.add(
            make_permission("hq-own", [make_rule("admin", "value.table.user.ids", value='[{"pk": %s}]' % superuser.pk)])
        )
        dept.rules.add(make_permission("dev-own", [make_rule("admin", "value.user.id")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert set(qs) == set(books)

    def test_dept_all_grant_grants_everything(self, dept, normal_user, books):
        """案例 1（新版）：部门单条「全部数据」授权 → 成员全量可见（旧且模式实现为空集）。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(make_permission("dept-all", [make_rule("admin", "value.all")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3

    def test_personal_all_grant_wins_over_dept_scope(self, dept, normal_user, superuser, books):
        """案例 2（新版）：个人「全部数据」授权胜出，不被部门范围吞掉。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(
            make_permission(
                "dept-scope", [make_rule("admin", "value.table.user.ids", value='[{"pk": %s}]' % superuser.pk)]
            )
        )
        normal_user.rules.add(make_permission("personal-all", [make_rule("admin", "value.all")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3

    def test_inactive_dept_grant_ignored(self, dept, normal_user, books):
        """绑定在停用部门上的授权不生效。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        dept.rules.add(make_permission("dept-off", [make_rule("admin", "value.all")]))
        dept.is_active = False
        dept.save(update_fields=["is_active"])
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_grant_on_descendant_dept_not_inherited_upward(self, dept, normal_user, books):
        """授权绑在下级部门不影响上级部门用户（部门继承只沿祖先链生效）。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        child = type(dept).objects.create(name="子部门", code="sub", parent=dept)
        child.rules.add(make_permission("sub-all", [make_rule("admin", "value.all")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_menu_scoped_grant_requires_menu_context(self, dept, normal_user, books, menu_factory):
        """绑定菜单的授权只在对应菜单上下文生效（通用授权不受影响）。"""
        menu = menu_factory("book-list", path="api/demo/book$", method="GET")
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        perm = make_permission("menu-scoped", [make_rule("admin", "value.all")])
        perm.menu.add(menu)
        dept.rules.add(perm)
        # 无菜单上下文：授权未命中 → 不可见
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0
        # 命中菜单上下文：授权生效
        normal_user.menu = str(menu.pk)
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 3

    def test_leader_departments_rule(self, dept, normal_user, superuser, books, upload_file):
        """value.leader.dept.ids：主管按「主管部门及下级」圈数据（dept_belong 维度）。"""
        child = type(dept).objects.create(name="子部门", code="sub-l", parent=dept)
        dept.leader = normal_user
        dept.save(update_fields=["leader"])
        b_lead = Book.objects.create(
            name="L", isbn="iL", author="aL", admin=superuser, admin2=superuser, file=upload_file, dept_belong=dept
        )
        b_other = Book.objects.create(
            name="O", isbn="iO", author="aO", admin=superuser, admin2=superuser, file=upload_file, dept_belong=child
        )
        normal_user.rules.add(make_permission("lead-dept", [make_rule("dept_belong", "value.leader.dept.ids")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert set(qs) == {b_lead, b_other}

    def test_leader_users_rule(self, dept, normal_user, superuser, books):
        """value.leader.user.ids：主管按「主管部门成员」圈数据（归属人维度）。"""
        dept.leader = normal_user
        dept.save(update_fields=["leader"])
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        # normal_user 主管 dept 且是 dept 成员 → 圈住 admin 为 normal_user 的书
        normal_user.rules.add(make_permission("lead-users", [make_rule("admin", "value.leader.user.ids")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert set(qs) == {books[2]}

    def test_non_leader_resolves_to_empty(self, normal_user, books):
        """非主管用户使用 leader 规则 → 解析为空集（恒假）。"""
        normal_user.rules.add(make_permission("lead-empty", [make_rule("admin", "value.leader.user.ids")]))
        assert get_filter_queryset(Book.objects.all(), normal_user).count() == 0

    def test_legacy_bad_rule_fail_closed(self, normal_user, books):
        """存量坏规则（字段名写错）读取侧 fail-closed，不再 500。"""
        normal_user.rules.add(make_permission("bad", [make_rule("creat0r", "value.user.id")]))
        qs = get_filter_queryset(Book.objects.all(), normal_user)
        assert qs.count() == 0
