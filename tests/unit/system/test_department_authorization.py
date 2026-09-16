# -*- coding: utf-8 -*-
"""部门编辑接口的授权写入（角色 / 数据权限）。

守护两件事：
1. 部门信息更新时携带的 roles/rules 会真正落库（历史实现静默丢弃，UI 上改了不生效）；
2. 未携带授权字段时保留既有授权，避免编辑部门信息时误清空。
"""

import pytest
from rest_framework.test import APIClient

from system.models import DataPermission, DeptInfo, UserInfo, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(db):
    return UserInfo.objects.create_superuser(username="dept_admin", password="Test@123456", nickname="超管")


@pytest.fixture
def dept(db):
    return DeptInfo.objects.create(name="研发部", code="rd")


@pytest.fixture
def grant_targets(db):
    return [
        UserRole.objects.create(name="只读角色", code="readonly"),
        DataPermission.objects.create(
            name="全部数据",
            rules=[
                {
                    "table": "system.userinfo",
                    "field": "id",
                    "type": "value.all",
                    "match": "all",
                    "value": "",
                    "exclude": False,
                }
            ],
        ),
    ]


def test_update_writes_roles_and_rules(admin, dept, grant_targets):
    role, rule = grant_targets
    client = APIClient()
    client.force_authenticate(user=admin)

    response = client.patch(
        f"/api/system/dept/{dept.pk}",
        {"name": "研发中心", "roles": [role.pk], "rules": [rule.pk]},
        format="json",
    )
    assert response.status_code == 200, response.content

    dept.refresh_from_db()
    assert dept.name == "研发中心"
    assert list(dept.roles.values_list("pk", flat=True)) == [role.pk]
    assert list(dept.rules.values_list("pk", flat=True)) == [rule.pk]


def test_update_without_authorization_fields_keeps_existing(admin, dept, grant_targets):
    role, rule = grant_targets
    dept.roles.set([role])
    dept.rules.set([rule])

    client = APIClient()
    client.force_authenticate(user=admin)
    response = client.patch(f"/api/system/dept/{dept.pk}", {"name": "研发二部"}, format="json")
    assert response.status_code == 200, response.content

    dept.refresh_from_db()
    assert dept.name == "研发二部"
    assert dept.roles.count() == 1
    assert dept.rules.count() == 1
