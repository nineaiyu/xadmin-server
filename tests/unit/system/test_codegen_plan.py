# -*- coding: utf-8 -*-
"""代码生成方案（服务端存储）单测。

口径钉死：
- 个人取值域：本人可见全部，他人仅见 ``is_shared``；
- 写操作仅本人（或超管），越权被拒且数据不被改动；
- 同名保存覆盖：同一用户重复保存同名方案原地更新，不产生重复行。
"""

import pytest

from identity.models import UserInfo
from system.models import CodegenPlan, Menu, MenuMeta

pytestmark = pytest.mark.django_db

PLANS_URL = "/api/system/codegen-plans"
LIST_PATH = "api/system/codegen-plans$"
DETAIL_PATH = "api/system/codegen-plans/(?P<pk>[^/.]+)$"


def _payload(name="方案A", **extra):
    data = {
        "name": name,
        "payload": {"model": "demo.Book", "with_tests": True},
    }
    data.update(extra)
    return data


def _grant(role, name, path, method):
    meta = MenuMeta.objects.create(title=name)
    perm = Menu.objects.create(name=name, path=path, method=method, menu_type=2, meta=meta)
    role.menu.add(perm)
    return perm


def grant_plan_permissions(role):
    """授权四个权限点（非超管访问的前置条件，与种子同名同路径）。"""
    _grant(role, "list:CodegenPlan", LIST_PATH, "GET")
    _grant(role, "create:CodegenPlan", LIST_PATH, "POST")
    _grant(role, "partialUpdate:CodegenPlan", DETAIL_PATH, "PATCH")
    _grant(role, "destroy:CodegenPlan", DETAIL_PATH, "DELETE")


class TestCrud:
    def test_create_and_list(self, auth_client, superuser):
        resp = auth_client.post(PLANS_URL, _payload(), format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["payload"]["model"] == "demo.Book"

        resp = auth_client.get(PLANS_URL)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["name"] == "方案A"

    def test_same_name_overwrites_without_duplication(self, auth_client):
        auth_client.post(PLANS_URL, _payload("方案A"), format="json")
        resp = auth_client.post(
            PLANS_URL, _payload("方案A", payload={"model": "demo.Book", "with_tests": False}), format="json"
        )
        assert resp.data["code"] == 1000, resp.data
        assert CodegenPlan.objects.filter(name="方案A").count() == 1
        assert resp.data["data"]["payload"]["with_tests"] is False

    def test_update_own_plan(self, auth_client):
        pk = auth_client.post(PLANS_URL, _payload(), format="json").data["data"]["pk"]
        resp = auth_client.patch(f"{PLANS_URL}/{pk}", {"name": "改名方案"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["name"] == "改名方案"

    def test_destroy_own_plan(self, auth_client):
        pk = auth_client.post(PLANS_URL, _payload(), format="json").data["data"]["pk"]
        resp = auth_client.delete(f"{PLANS_URL}/{pk}")
        assert resp.data["code"] == 1000, resp.data
        assert CodegenPlan.objects.count() == 0

    def test_shared_flag_round_trip(self, auth_client):
        resp = auth_client.post(PLANS_URL, _payload(is_shared=True), format="json")
        assert resp.data["data"]["is_shared"] is True


class TestPersonalScope:
    def _user_with_role(self, role, username):
        user = UserInfo.objects.create_user(username=username, password="Test@123456")
        user.roles.add(role)
        return user

    def test_other_user_sees_only_shared(self, api_client, normal_user, role):
        grant_plan_permissions(role)
        CodegenPlan.objects.create(creator=normal_user, name="私有", payload={"model": "a"})
        CodegenPlan.objects.create(creator=normal_user, name="共享", payload={"model": "b"}, is_shared=True)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(PLANS_URL)
        assert resp.data["data"]["total"] == 2

        # 非超管未配置字段白名单时输出零字段（fail-closed），取值域以 total 断言
        other = self._user_with_role(role, "other_plan_user")
        api_client.force_authenticate(user=other)
        resp = api_client.get(PLANS_URL)
        assert resp.data["data"]["total"] == 1

    def test_other_user_cannot_modify_or_delete(self, api_client, normal_user, role):
        grant_plan_permissions(role)
        own = CodegenPlan.objects.create(creator=normal_user, name="私有", payload={"model": "a"})

        other = self._user_with_role(role, "other_plan_writer")
        api_client.force_authenticate(user=other)
        # 越权改 / 删：取值域外退出 queryset（404 归一为可读错误码），数据不被改动
        resp = api_client.patch(f"{PLANS_URL}/{own.pk}", {"name": "篡改"}, format="json")
        assert resp.status_code in (400, 403, 404), resp.status_code
        resp = api_client.delete(f"{PLANS_URL}/{own.pk}")
        assert resp.status_code in (400, 403, 404), resp.status_code
        own.refresh_from_db()
        assert own.name == "私有"

    def test_owner_can_modify_shared_plan(self, api_client, normal_user, role):
        grant_plan_permissions(role)
        own = CodegenPlan.objects.create(creator=normal_user, name="共享", payload={"model": "a"}, is_shared=True)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{PLANS_URL}/{own.pk}", {"description": "备注"}, format="json")
        assert resp.data["code"] == 1000, resp.data

    def test_superuser_all_scope(self, auth_client, superuser, normal_user):
        CodegenPlan.objects.create(creator=normal_user, name="他人私有", payload={"model": "a"})
        resp = auth_client.get(PLANS_URL, {"all": "1"})
        assert resp.data["data"]["total"] == 1
