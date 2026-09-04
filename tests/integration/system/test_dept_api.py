# -*- coding: utf-8 -*-
"""system 部门接口集成测试。"""
import pytest

from system.models import DeptInfo, UserInfo

pytestmark = pytest.mark.django_db

DEPT_URL = "/api/system/dept"


def _create_dept(auth_client, name="测试部门", code="test_dept", **kwargs):
    payload = {"name": name, "code": code}
    payload.update(kwargs)
    resp = auth_client.post(DEPT_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestDeptCrudSmoke:
    def test_create_list_retrieve_patch_delete(self, auth_client):
        pk = _create_dept(auth_client)

        resp = auth_client.get(DEPT_URL, {"name": "测试"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] >= 1
        assert any(d["pk"] == pk for d in resp.data["data"]["results"])

        resp = auth_client.get(f"{DEPT_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["code"] == "test_dept"

        resp = auth_client.patch(f"{DEPT_URL}/{pk}", {"description": "新描述"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["description"] == "新描述"

        resp = auth_client.delete(f"{DEPT_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not DeptInfo.objects.filter(pk=pk).exists()

    def test_filter_by_name(self, auth_client):
        _create_dept(auth_client, name="研发部", code="dev")
        _create_dept(auth_client, name="产品部", code="pro")
        resp = auth_client.get(DEPT_URL, {"name": "研发"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["code"] == "dev"

    def test_dynamic_pagination_returns_all(self, auth_client):
        _create_dept(auth_client, name="部门A", code="dept_a")
        _create_dept(auth_client, name="部门B", code="dept_b")
        resp = auth_client.get(DEPT_URL)
        assert resp.status_code == 200
        # DynamicPageNumber(1000)：一页返回全部，不拆分多页
        assert resp.data["data"]["total"] == 2
        assert len(resp.data["data"]["results"]) == 2


class TestDeptUserBinding:
    def test_user_list_filter_by_dept(self, auth_client):
        dept_pk = _create_dept(auth_client, name="研发部", code="dev")
        payload = {
            "username": "lisi",
            "nickname": "李四",
            "password": "Test@123456",
            "dept": dept_pk,
        }
        resp = auth_client.post("/api/system/user", payload, format="json")
        assert resp.status_code == 200, resp.data

        resp = auth_client.get("/api/system/user", {"dept": str(dept_pk)})
        assert resp.data["data"]["total"] == 1
        results = resp.data["data"]["results"]
        assert results[0]["username"] == "lisi"
        assert str(results[0]["dept"]["pk"]) == dept_pk

    def test_dept_delete_has_no_user_ref(self, auth_client):
        """部门删除时未挂用户则可直接删除。"""
        dept_pk = _create_dept(auth_client)
        assert UserInfo.objects.filter(dept_id=dept_pk).count() == 0
        resp = auth_client.delete(f"{DEPT_URL}/{dept_pk}")
        assert resp.data["code"] == 1000