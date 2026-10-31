# -*- coding: utf-8 -*-
"""在线会话的默认数据权限。

口径：显式 DataPermission 授权优先（含「全部数据」）；非超管对该资源没有任何适用
授权时回落到「本人 + 本部门（含子部门）」，而不是空集；超管不受限。跨部门强退 403。
"""

import pytest
from django.core.cache import cache as django_cache

from identity.models import DeptInfo, UserInfo, UserSession
from system.models import DataPermission

pytestmark = pytest.mark.django_db

ONLINE_LIST_PATH = "api/identity/online$"
ONLINE_FORCE_PATH = "api/identity/online/force-logout$"
LIST_URL = "/api/identity/online"


@pytest.fixture
def online_scope(db, normal_user):
    """本部门（含子部门）与跨部门的会话：normal_user 本人 / 下属 / 外部。"""
    root = DeptInfo.objects.create(name="在线会话-研发部", code="online-dev")
    child = DeptInfo.objects.create(name="在线会话-前端组", code="online-dev-fe", parent=root)
    other = DeptInfo.objects.create(name="在线会话-财务部", code="online-fin")
    normal_user.dept = root
    normal_user.save(update_fields=["dept"])
    teammate = UserInfo.objects.create_user(username="online_teammate", password="Test@123456", dept=child)
    outsider = UserInfo.objects.create_user(username="online_outsider", password="Test@123456", dept=other)
    sessions = {
        "me": UserSession.objects.create(creator=normal_user, dept_belong=root, channel_name=""),
        "teammate": UserSession.objects.create(creator=teammate, dept_belong=child, channel_name=""),
        "outsider": UserSession.objects.create(creator=outsider, dept_belong=other, channel_name=""),
    }
    return normal_user, teammate, outsider, sessions


def _grant_permission(role, menu_factory, name, path, method):
    role.menu.add(menu_factory(name, path=path, method=method))
    # 权限解析缓存 24h：授权变更后清空
    django_cache.clear()


def _grant_all_data(user, table):
    user.rules.add(
        DataPermission.objects.create(
            name=f"online-all-{table}",
            rules=[{"table": table, "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}],
        )
    )
    django_cache.clear()


def _visible_rows(response):
    # 非超管未配置字段权限时序列化器按 fail-closed 裁空字段，行数仍反映数据权限范围
    return len(response.data["data"]["results"])


class TestOnlineDefaultDataScope:
    def test_superuser_sees_all_sessions(self, auth_client, online_scope):
        response = auth_client.get(LIST_URL)
        assert response.data["code"] == 1000
        assert _visible_rows(response) == 3

    def test_non_superuser_without_grant_sees_self_and_dept_subtree(
        self, api_client, normal_user, role, menu_factory, online_scope
    ):
        _grant_permission(role, menu_factory, "list:SystemUserOnline", ONLINE_LIST_PATH, "GET")
        api_client.force_authenticate(user=normal_user)
        response = api_client.get(LIST_URL)
        assert response.data["code"] == 1000
        assert _visible_rows(response) == 2

    def test_explicit_all_data_grant_sees_everything(self, api_client, normal_user, role, menu_factory, online_scope):
        _grant_permission(role, menu_factory, "list:SystemUserOnline", ONLINE_LIST_PATH, "GET")
        _grant_all_data(normal_user, "identity.usersession")
        api_client.force_authenticate(user=normal_user)
        response = api_client.get(LIST_URL)
        assert response.data["code"] == 1000
        assert _visible_rows(response) == 3

    def test_cross_department_force_logout_is_forbidden(
        self, api_client, normal_user, role, menu_factory, online_scope
    ):
        _added, _teammate, outsider, _sessions = online_scope
        _grant_permission(role, menu_factory, "forceLogout:SystemUserOnline", ONLINE_FORCE_PATH, "POST")
        api_client.force_authenticate(user=normal_user)
        response = api_client.post("/api/identity/online/force-logout", {"user_pk": outsider.pk}, format="json")
        assert response.status_code == 403
