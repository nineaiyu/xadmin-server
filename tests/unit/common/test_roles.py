# -*- coding: utf-8 -*-
"""用户生效角色解析：直挂 ∪ 部门挂角色，且过滤停用/软删角色。"""

import pytest

from common.core.roles import iter_user_roles, role_members_q, user_role_codes, user_role_pks
from identity.models import DeptInfo, UserInfo, UserRole

pytestmark = pytest.mark.django_db


def test_user_roles_include_active_department_roles():
    dept = DeptInfo.objects.create(name="研发部", code="role_rd")
    dept_role = UserRole.objects.create(name="部门角色", code="dept_role")
    direct_role = UserRole.objects.create(name="直挂角色", code="direct_role")
    inactive = UserRole.objects.create(name="停用角色", code="inactive_role", is_active=False)
    deleted = UserRole.objects.create(name="已删角色", code="deleted_role")
    deleted.delete()
    dept.roles.add(dept_role, inactive, deleted)
    user = UserInfo.objects.create_user(username="role_user", password="Test@123456", dept=dept)
    user.roles.add(direct_role, inactive)

    assert {role.code for role in iter_user_roles(user)} == {"dept_role", "direct_role"}
    assert user_role_codes(user) == {"dept_role", "direct_role"}
    assert user_role_pks(user) == {dept_role.pk, direct_role.pk}


def test_inactive_department_does_not_grant_roles():
    dept = DeptInfo.objects.create(name="停用部门", code="role_rd_off", is_active=False)
    role = UserRole.objects.create(name="部门角色", code="dept_role_off")
    dept.roles.add(role)
    user = UserInfo.objects.create_user(username="role_user_off", password="Test@123456", dept=dept)

    assert iter_user_roles(user) == []


def test_role_members_q_matches_direct_and_department_members():
    dept = DeptInfo.objects.create(name="市场部", code="role_mkt")
    role = UserRole.objects.create(name="市场角色", code="mkt_role")
    dept.roles.add(role)
    direct = UserInfo.objects.create_user(username="role_direct", password="Test@123456")
    direct.roles.add(role)
    member = UserInfo.objects.create_user(username="role_member", password="Test@123456", dept=dept)
    outsider = UserInfo.objects.create_user(username="role_outsider", password="Test@123456")

    matched = set(UserInfo.objects.filter(role_members_q(["mkt_role"])).values_list("pk", flat=True))
    assert matched == {direct.pk, member.pk}
    assert outsider.pk not in matched
