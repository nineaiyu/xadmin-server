# -*- coding: utf-8 -*-
"""system 角色接口集成测试。"""
import pytest

from system.models import UserRole

pytestmark = pytest.mark.django_db

ROLE_URL = "/api/system/role"


def _create_role(auth_client, name="测试角色", code="test_role", **kwargs):
    payload = {"name": name, "code": code, "fields": {}}
    payload.update(kwargs)
    resp = auth_client.post(ROLE_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestRoleCrudSmoke:
    def test_create_list_retrieve_patch_delete(self, auth_client):
        pk = _create_role(auth_client)

        resp = auth_client.get(ROLE_URL, {"name": "测试"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] >= 1
        assert any(r["pk"] == pk for r in resp.data["data"]["results"])

        resp = auth_client.get(f"{ROLE_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["code"] == "test_role"

        resp = auth_client.patch(
            f"{ROLE_URL}/{pk}", {"description": "新描述", "fields": {}}, format="json"
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["description"] == "新描述"

        resp = auth_client.delete(f"{ROLE_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not UserRole.objects.filter(pk=pk).exists()

    def test_create_duplicate_code(self, auth_client):
        _create_role(auth_client, name="角色A", code="dup_code")
        resp = auth_client.post(
            ROLE_URL, {"name": "角色B", "code": "dup_code", "fields": {}}, format="json"
        )
        assert resp.data["code"] != 1000

    def test_filter_by_code(self, auth_client):
        _create_role(auth_client, name="角色A", code="role_a")
        _create_role(auth_client, name="角色B", code="role_b")
        resp = auth_client.get(ROLE_URL, {"code": "role_a"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["code"] == "role_a"


class TestRoleMenuBinding:
    def test_role_create_with_menus(self, auth_client, menu_factory):
        permission_menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        pk = _create_role(auth_client, menu=[permission_menu.pk])

        resp = auth_client.get(f"{ROLE_URL}/{pk}")
        assert resp.status_code == 200
        menu_pks = [m["pk"] for m in resp.data["data"]["menu"]]
        assert permission_menu.pk in menu_pks

    def test_role_bind_menu_via_update(self, auth_client, menu_factory):
        pk = _create_role(auth_client)
        permission_menu = menu_factory("p-list", path="api/demo/book$", method="GET")

        resp = auth_client.patch(
            f"{ROLE_URL}/{pk}", {"menu": [permission_menu.pk], "fields": {}}, format="json"
        )
        assert resp.status_code == 200, resp.data
        menu_pks = [m["pk"] for m in resp.data["data"]["menu"]]
        assert permission_menu.pk in menu_pks