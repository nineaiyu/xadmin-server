# -*- coding: utf-8 -*-
"""当前用户信息端点（/api/system/userinfo）载荷回归。

本人信息无泄露面：超管标记直接透出，前端按 `is_superuser` 参与实例评论
删除按钮等显隐判断（作者 ∪ 超管）。
"""

import pytest

pytestmark = pytest.mark.django_db

USERINFO_URL = "/api/system/userinfo"


class TestUserinfoPayload:
    def test_is_superuser_true_for_superuser(self, auth_client):
        resp = auth_client.get(USERINFO_URL)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["is_superuser"] is True

    def test_is_superuser_false_for_normal_user(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(USERINFO_URL)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["is_superuser"] is False
        # 基础载荷字段仍齐全（超管标记为新增键，不影响既有消费面）
        assert {"pk", "username", "nickname", "email", "phone"} <= set(resp.data["data"])
