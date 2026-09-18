# -*- coding: utf-8 -*-
"""动态表单选人控件数据源（user-options）集成测试。

覆盖：关键字（用户名/昵称）搜索、字段收敛（仅 pk/用户名/昵称）、空关键字不出全量、
pks 回显（编辑既有提交）、非法参数忽略、无权限用户 fail-closed。
"""

import pytest

from system.models import UserInfo

pytestmark = pytest.mark.django_db

USER_OPTIONS_URL = "/api/system/dynamic-form-submissions/user-options"


@pytest.fixture
def picker_users(db):
    return [
        UserInfo.objects.create_superuser(
            username="picker_a", email="picker_a@example.com", password="Test@123456", nickname="甲"
        ),
        UserInfo.objects.create_superuser(
            username="picker_b", email="picker_b@example.com", password="Test@123456", nickname="乙"
        ),
    ]


class TestUserOptions:
    def test_keyword_search_returns_limited_fields(self, auth_client, picker_users):
        resp = auth_client.get(USER_OPTIONS_URL, {"keyword": "picker_a"})
        assert resp.data["code"] == 1000, resp.data
        rows = resp.data["data"]
        assert [row["username"] for row in rows] == ["picker_a"]
        # 字段收敛：仅主键与基本展示字段，不带邮箱/部门等档案
        assert set(rows[0]) == {"pk", "username", "nickname"}

    def test_keyword_matches_nickname(self, auth_client, picker_users):
        resp = auth_client.get(USER_OPTIONS_URL, {"keyword": "乙"})
        assert [row["username"] for row in resp.data["data"]] == ["picker_b"]

    def test_empty_keyword_returns_empty(self, auth_client, picker_users):
        """无关键字不做通讯录全量枚举。"""
        resp = auth_client.get(USER_OPTIONS_URL)
        assert resp.data["data"] == []

    def test_pks_lookup_for_edit_echo(self, auth_client, picker_users):
        pks = ",".join(str(user.pk) for user in picker_users)
        resp = auth_client.get(USER_OPTIONS_URL, {"pks": pks})
        assert {row["username"] for row in resp.data["data"]} == {"picker_a", "picker_b"}

    def test_invalid_pks_ignored(self, auth_client, picker_users):
        resp = auth_client.get(USER_OPTIONS_URL, {"pks": "abc,-1,"})
        assert resp.data["data"] == []

    def test_permission_fail_closed_without_menu(self, api_client, normal_user, picker_users):
        """无对应权限菜单的用户一律拒绝（与 list 权限同口径，未授权不放行）。"""
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(USER_OPTIONS_URL, {"keyword": "picker"})
        assert resp.status_code == 403, resp.data
