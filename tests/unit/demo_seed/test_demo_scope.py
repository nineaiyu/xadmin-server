# -*- coding: utf-8 -*-
"""演示账号用户范围与超管目标保护。

覆盖两层防线：
1. ``UserViewSet.get_queryset``：演示角色只能看到本部门子树内的用户；
2. ``UserViewSet`` / ``UserSerializer``：非超管不能修改超管账号。
"""

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import PermissionDenied

from common.core.filter import get_filter_queryset
from identity.models import DeptInfo, UserInfo, UserRole
from identity.serializers.user import UserSerializer
from identity.views.admin.user import UserViewSet
from system.models import DataPermission

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_tree(db):
    demo = DeptInfo.objects.create(name="演示部门", code="demo", is_active=True)
    rd = DeptInfo.objects.create(name="示例-研发部", code="demo_rd", parent=demo, is_active=True)
    xadmin = DeptInfo.objects.create(name="xAdmin管理团队", code="xadmin", is_active=True)
    role = UserRole.objects.create(name="演示模式", code="demo", is_active=True)

    demo_admin = UserInfo.objects.create_user(username="demo_admin", password="Test@123456", dept=demo)
    demo_admin.roles.add(role)
    demo_staff = UserInfo.objects.create_user(username="demo_staff", password="Test@123456", dept=rd)
    superuser = UserInfo.objects.create_superuser(
        username="xadmin", email="xadmin@test.local", password="Test@123456", dept=xadmin
    )
    return demo_admin, demo_staff, superuser


def _view(user):
    view = UserViewSet()
    view.request = RequestFactory().get("/api/identity/user")
    view.request.user = user
    view.action = "list"
    view.format_kwarg = None
    return view


def test_demo_user_queryset_is_limited_to_dept_subtree(demo_tree):
    demo_admin, demo_staff, superuser = demo_tree

    names = set(_view(demo_admin).get_queryset().values_list("username", flat=True))
    assert names == {"demo_admin", "demo_staff"}
    assert superuser.username not in names


def test_non_demo_user_queryset_is_not_limited(demo_tree):
    demo_admin, _demo_staff, superuser = demo_tree
    normal = UserInfo.objects.create_user(username="normal", password="Test@123456")

    names = set(_view(normal).get_queryset().values_list("username", flat=True))
    assert {"normal", "demo_admin", superuser.username} <= names


def test_superuser_queryset_is_not_limited(demo_tree):
    _demo_admin, demo_staff, superuser = demo_tree

    names = set(_view(superuser).get_queryset().values_list("username", flat=True))
    assert {"demo_admin", "demo_staff", "xadmin"} <= names


def test_demo_user_without_dept_sees_nobody(demo_tree):
    demo_admin, _demo_staff, _superuser = demo_tree
    demo_admin.dept = None
    demo_admin.save(update_fields=["dept"])

    assert not _view(demo_admin).get_queryset().exists()


def test_demo_dept_data_permission_limits_userinfo(demo_tree):
    """种子中的「演示部门用户范围」数据权限只放行演示子树用户。"""

    demo_admin, demo_staff, superuser = demo_tree
    permission = DataPermission.objects.create(
        name="演示部门用户范围",
        rules=[
            {
                "type": "value.user.dept.ids",
                "field": "dept",
                "match": "in",
                "table": "identity.userinfo",
                "value": "",
                "exclude": False,
            },
            {
                "type": "value.user.dept.ids",
                "field": "dept_belong",
                "match": "in",
                "table": "identity.userinfo",
                "value": "",
                "exclude": False,
            },
        ],
    )
    demo_admin.dept.rules.add(permission)

    names = set(get_filter_queryset(UserInfo.objects.all(), demo_admin).values_list("username", flat=True))
    assert {"demo_admin", "demo_staff"} <= names
    assert superuser.username not in names


def test_demo_write_scope_uses_creator_dept_when_dept_belong_empty(demo_tree):
    """新建数据显式带 creator 时，creator.dept 兜底保证演示管理员仍可见。"""

    from demo.models import Book

    demo_admin, demo_staff, superuser = demo_tree
    permission = DataPermission.objects.create(
        name="演示写入数据范围",
        rules=[
            {
                "type": "value.user.dept.ids",
                "field": "dept_belong",
                "match": "in",
                "table": "demo.book",
                "value": "",
                "exclude": False,
            },
            {
                "type": "value.user.dept.ids",
                "field": "creator__dept",
                "match": "in",
                "table": "demo.book",
                "value": "",
                "exclude": False,
            },
        ],
    )
    demo_admin.dept.rules.add(permission)

    demo_book = Book.objects.create(
        name="演示书", isbn="1", author="a", admin=superuser, admin2=superuser, creator=demo_staff
    )
    foreign_book = Book.objects.create(
        name="外部书", isbn="2", author="a", admin=superuser, admin2=superuser, creator=superuser
    )

    names = set(get_filter_queryset(Book.objects.all(), demo_admin).values_list("name", flat=True))
    assert demo_book.name in names
    assert foreign_book.name not in names


def test_non_superuser_cannot_mutate_superuser(demo_tree):
    demo_admin, _demo_staff, superuser = demo_tree
    view = _view(demo_admin)

    with pytest.raises(PermissionDenied):
        view._assert_target_mutable(superuser)
    with pytest.raises(PermissionDenied):
        view.assert_can_upload(superuser)
    with pytest.raises(PermissionDenied):
        view.assert_can_change_permissions(superuser)


def test_superuser_can_mutate_superuser(demo_tree):
    _demo_admin, _demo_staff, superuser = demo_tree
    view = _view(superuser)

    view._assert_target_mutable(superuser)
    view.assert_can_upload(superuser)
    view.assert_can_change_permissions(superuser)


def test_user_serializer_rejects_non_superuser_updating_superuser(demo_tree):
    demo_admin, _demo_staff, superuser = demo_tree
    request = RequestFactory().patch("/api/identity/user/1")
    request.user = demo_admin

    serializer = UserSerializer(superuser, data={"nickname": "hijack"}, partial=True, context={"request": request})
    with pytest.raises(PermissionDenied):
        serializer.is_valid(raise_exception=True)


def test_user_serializer_allows_superuser_updating_superuser(demo_tree):
    _demo_admin, _demo_staff, superuser = demo_tree
    request = RequestFactory().patch("/api/identity/user/1")
    request.user = superuser

    serializer = UserSerializer(superuser, data={"nickname": "ok"}, partial=True, context={"request": request})
    assert serializer.is_valid(raise_exception=True)
