# -*- coding: utf-8 -*-
"""system 部门接口集成测试。"""

import pytest

from identity.models import DeptInfo, UserInfo
from system.models import DataPermission, FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

DEPT_URL = "/api/system/dept"
DEPT_DETAIL_PATH = "api/system/dept/(?P<pk>[^/.]+)$"


@pytest.fixture(autouse=True)
def _plaintext_create_mode(settings):
    """本文件建号用明文密码提交（导入/脚本等非浏览器客户端的形态），
    属建号密码加密开关（SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED）关闭的明文模式。"""
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False


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


class TestDeptParentWriteContract:
    """parent 写契约：区分「未提交」与「显式置空」，创建保留默认挂操作者部门。"""

    def test_patch_explicit_null_parent_clears(self, auth_client, dept):
        """PATCH 显式提交 parent=null 为置空语义：部门提升为顶层。"""
        child = DeptInfo.objects.create(name="子部门", code="dev-child", parent=dept)
        resp = auth_client.patch(f"{DEPT_URL}/{child.pk}", {"parent": None}, format="json")
        assert resp.status_code == 200, resp.data
        child.refresh_from_db()
        assert child.parent_id is None

    def test_patch_without_parent_keeps_existing_parent(self, auth_client, dept):
        """PATCH 未提交 parent 时原上级保持不变。"""
        child = DeptInfo.objects.create(name="子部门", code="dev-child", parent=dept)
        resp = auth_client.patch(f"{DEPT_URL}/{child.pk}", {"name": "子部门改名"}, format="json")
        assert resp.status_code == 200, resp.data
        child.refresh_from_db()
        assert child.parent_id == dept.pk

    def test_patch_without_parent_keeps_top_level(self, auth_client, dept):
        """顶层部门 PATCH 未提交 parent 不得被静默改挂到操作者部门。"""
        resp = auth_client.patch(f"{DEPT_URL}/{dept.pk}", {"name": "研发总部"}, format="json")
        assert resp.status_code == 200, resp.data
        dept.refresh_from_db()
        assert dept.parent_id is None

    def test_create_without_parent_defaults_to_operator_dept(self, auth_client, superuser, dept):
        """创建未提交 parent 保留既有默认：挂到操作者所在部门。"""
        superuser.dept = dept
        superuser.save(update_fields=["dept"])
        resp = auth_client.post(DEPT_URL, {"name": "市场部", "code": "market"}, format="json")
        assert resp.status_code == 200, resp.data
        created = DeptInfo.objects.get(code="market")
        assert created.parent_id == dept.pk


def _make_dept_field_whitelist(role, menu, fields=("pk", "name", "leader", "parent")):
    """部门模型字段白名单（fail-closed：无白名单=字段被整体裁剪，写口不生效）。"""
    model_field = ModelLabelField.objects.create(
        name="identity.deptinfo", label="部门", field_type=ModelLabelField.FieldChoices.ROLE
    )
    children = [
        ModelLabelField.objects.create(
            name=f, label=f, parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
        )
        for f in fields
    ]
    fp = FieldPermission.objects.create(role=role, menu=menu)
    fp.field.add(*children)
    return fp


class TestDeptLeaderDataScope:
    """leader 写入口数据范围校验：范围外主管写入被拒，范围内放行。

    关联字段取值域本身经数据权限过滤（BasePrimaryKeyRelatedField.get_queryset），
    序列化器 validate 的范围校验为同口径防线；本类从 API 层钉住最终行为。
    """

    @pytest.fixture
    def scoped_client(self, api_client, dept, normal_user, role, menu_factory):
        """操作者：非超管，仅可见 code=dev 部门与 username=zhangsan 的用户。"""
        menu = menu_factory(name="p-dept-patch", path=DEPT_DETAIL_PATH, method="PATCH")
        role.menu.add(menu)
        _make_dept_field_whitelist(role, menu)
        normal_user.rules.add(
            DataPermission.objects.create(
                name="部门可见",
                rules=[
                    {
                        "table": "identity.deptinfo",
                        "field": "code",
                        "type": "value.text",
                        "value": "dev",
                        "match": "exact",
                    }
                ],
            ),
            DataPermission.objects.create(
                name="范围内用户可见",
                rules=[
                    {
                        "table": "identity.userinfo",
                        "field": "username",
                        "type": "value.text",
                        "value": "zhangsan",
                        "match": "exact",
                    }
                ],
            ),
        )
        api_client.force_authenticate(user=normal_user)
        return api_client

    def test_leader_out_of_scope_rejected(self, scoped_client, dept):
        outsider = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        resp = scoped_client.patch(f"{DEPT_URL}/{dept.pk}", {"leader": str(outsider.pk)}, format="json")
        assert resp.status_code == 400, resp.data
        dept.refresh_from_db()
        assert dept.leader_id is None

    def test_leader_within_scope_allowed(self, scoped_client, dept, normal_user):
        resp = scoped_client.patch(f"{DEPT_URL}/{dept.pk}", {"leader": str(normal_user.pk)}, format="json")
        assert resp.status_code == 200, resp.data
        dept.refresh_from_db()
        assert dept.leader_id == normal_user.pk
