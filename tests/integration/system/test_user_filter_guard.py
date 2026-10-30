# -*- coding: utf-8 -*-
"""用户列表查询参数守卫：非法 UUID 是客户端错误（400），不是 500。

`role` 是角色列表「用户数」跳转带过来的 UUID 主键参数；`CharFilter` 直传 ORM 时
`UUIDField.to_python` 会在查询集求值阶段抛 ValidationError → 500（错误页吞掉分页
与筛选条件，前端只能看到「服务器错误」）。
"""

import pytest

pytestmark = pytest.mark.django_db

USER_URL = "/api/identity/user"


class TestInvalidUuidQuery:
    def test_invalid_role_uuid_returns_400(self, auth_client):
        resp = auth_client.get(USER_URL, {"role": "not-a-uuid"})
        assert resp.status_code == 400, resp.data

    def test_empty_role_value_ignored(self, auth_client):
        resp = auth_client.get(USER_URL, {"role": ""})
        assert resp.status_code == 200, resp.data

    def test_valid_role_uuid_returns_page(self, auth_client):
        resp = auth_client.get(USER_URL, {"role": "00000000-0000-0000-0000-000000000000"})
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert resp.data["data"]["total"] == 0
