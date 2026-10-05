# -*- coding: utf-8 -*-
"""部门管理员集成：任命端点 / 数据权限收敛 / 写侧载荷范围校验。

覆盖三类面：
1. 任命端点（`assign-managers`）：权限门、装配生效（角色 + 预置规则）、解任回收、
   非法主键可读报错；
2. 被任命者的数据可见性：用户列表按管辖范围收敛、管辖外对象按 pk 写被拒；
3. 写侧载荷范围校验（D2）：归属字段取值范围、关系字段可授权池、超管豁免。
"""

import pytest

from identity.models import DeptInfo, DeptManagerAssignment, UserInfo, UserRole
from identity.utils.dept_managers import DEPT_MANAGER_ROLE_CODE, sync_manager_assembly
from system.models import DataPermission, FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

DEPT_URL = "/api/system/dept"
USER_URL = "/api/system/user"

USER_LIST_PATH = "api/system/user$"
USER_DETAIL_PATH = "api/system/user/(?P<pk>[^/.]+)$"
DEPT_DETAIL_PATH = "api/system/dept/(?P<pk>[^/.]+)$"


def grant_menu(role, menu_factory, path, method, name=None):
    """给角色授予一条 PERMISSION 类型菜单（生产菜单 path 正则惯例）。"""
    menu = menu_factory(name or f"p-{method}-{path}", path=path, method=method)
    role.menu.add(menu)
    return menu


def make_user_field_whitelist(role, menu, fields=("pk", "username", "nickname", "dept")):
    """用户模型字段白名单（fail-closed：无白名单=响应全裁剪）。"""
    model_field = ModelLabelField.objects.create(
        name="identity.userinfo", label="用户", field_type=ModelLabelField.FieldChoices.ROLE
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


class TestAssignManagersAPI:
    URL = f"{DEPT_URL}/{{pk}}/assign-managers"

    def test_assign_requires_permission(self, api_client, normal_user, dept):
        """无 assignManagers 权限点 → 403。"""
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(self.URL.format(pk=dept.pk), {"add": []}, format="json")
        assert resp.status_code == 403

    def test_superuser_assign_and_assembly(self, api_client, superuser, dept, normal_user):
        """超管任命：through 行 + 预置角色 + 两条预置数据权限规则一步装配。"""
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(self.URL.format(pk=dept.pk), {"add": [normal_user.pk]}, format="json")
        assert resp.status_code == 200
        assert [m["pk"] for m in resp.data["data"]["managers"]] == [normal_user.pk]

        assert DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).exists()
        assert normal_user.roles.filter(code=DEPT_MANAGER_ROLE_CODE).exists()
        assert normal_user.rules.filter(name__startswith="部门管理-").count() == 2

    def test_remove_recycles_assembly(self, api_client, superuser, dept, normal_user):
        """解任：管理关系、角色成员与预置规则一并回收。"""
        api_client.force_authenticate(user=superuser)
        api_client.post(self.URL.format(pk=dept.pk), {"add": [normal_user.pk]}, format="json")

        resp = api_client.post(self.URL.format(pk=dept.pk), {"remove": [normal_user.pk]}, format="json")
        assert resp.status_code == 200
        assert resp.data["data"]["managers"] == []
        assert not DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).exists()
        assert not normal_user.roles.filter(code=DEPT_MANAGER_ROLE_CODE).exists()
        assert normal_user.rules.filter(name__startswith="部门管理-").count() == 0

    def test_invalid_pk_readable_error(self, api_client, superuser, dept):
        """非法主键形态：可读业务失败（1001），不落 500。"""
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(self.URL.format(pk=dept.pk), {"add": ["not-a-number"]}, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1001


class TestManagerDataScope:
    """被任命者的数据可见性：列表按管辖收敛、管辖外对象写被拒。"""

    def _setup_manager(self, dept, manager, role, menu_factory):
        DeptManagerAssignment.objects.create(dept=dept, user=manager)
        sync_manager_assembly(manager)
        list_menu = grant_menu(role, menu_factory, USER_LIST_PATH, "GET", name="p-user-list")
        make_user_field_whitelist(role, list_menu)

    def test_manager_sees_only_scoped_members(self, api_client, dept, normal_user, role, menu_factory):
        member = UserInfo.objects.create_user(username="member1", password="Test@123456")
        member.dept = dept
        member.save(update_fields=["dept"])
        outsider = UserInfo.objects.create_user(username="outsider1", password="Test@123456")

        self._setup_manager(dept, normal_user, role, menu_factory)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(USER_URL)
        assert resp.status_code == 200
        pks = {row["pk"] for row in resp.data["data"]["results"]}
        assert member.pk in pks
        assert outsider.pk not in pks

    def test_manager_update_out_of_scope_denied(self, api_client, dept, normal_user, role, menu_factory):
        """对象级写侧（既有机制回归）：管辖外用户按 pk 修改 → 400。"""
        outsider = UserInfo.objects.create_user(username="outsider2", password="Test@123456")
        self._setup_manager(dept, normal_user, role, menu_factory)
        grant_menu(role, menu_factory, USER_DETAIL_PATH, "PATCH", name="p-user-patch")

        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{USER_URL}/{outsider.pk}", {"nickname": "篡改"}, format="json")
        assert resp.status_code == 400
        outsider.refresh_from_db()
        assert outsider.nickname != "篡改"


class TestManagedScopeAPI:
    def test_managed_lists_direct_and_subtree(self, api_client, dept, normal_user, role, menu_factory):
        """我的管辖：直接任命部门 + 全部下级；统计、成员数与主管/管理员随行。"""
        child = DeptInfo.objects.create(name="子部门", code="managed-child", parent=dept)
        manager_member = UserInfo.objects.create_user(username="managed1", password="Test@123456")
        manager_member.dept = child
        manager_member.save(update_fields=["dept"])
        DeptManagerAssignment.objects.create(dept=dept, user=normal_user)
        dept.leader = manager_member
        dept.save(update_fields=["leader"])
        grant_menu(role, menu_factory, DEPT_DETAIL_PATH, "GET", name="p-dept-detail")

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(f"{DEPT_URL}/managed")
        assert resp.status_code == 200
        data = resp.data["data"]
        rows = {str(row["pk"]): row for row in data["depts"]}
        assert {str(dept.pk), str(child.pk)} <= set(rows)
        assert rows[str(dept.pk)]["is_direct"] is True
        assert rows[str(child.pk)]["is_direct"] is False
        assert data["dept_count"] == 2
        assert data["user_count"] >= 1
        # 联系人信息：主管与管理员清单（供管辖页直接展示）
        assert rows[str(dept.pk)]["leader"]["pk"] == manager_member.pk
        assert [m["pk"] for m in rows[str(dept.pk)]["managers"]] == [normal_user.pk]
        assert rows[str(child.pk)]["leader"] is None
        assert rows[str(child.pk)]["managers"] == []


class TestWriteScopeGuardAPI:
    """写侧载荷范围校验（D2，走真实链路）：归属字段与关系字段的取值范围收敛。

    字段权限为 fail-closed 白名单（无白名单 = 读写字段整体裁剪）：用例先给
    partialUpdate 菜单配字段白名单，再验证范围校验真正生效（400 且零副作用）。
    """

    def _setup_manager(self, dept, manager, role, menu_factory, fields=("nickname", "dept", "roles")):
        """真实装配场景：任命 + 预置规则（用户面可见=管辖成员）+ PATCH 菜单字段白名单。"""
        DeptManagerAssignment.objects.create(dept=dept, user=manager)
        sync_manager_assembly(manager)
        patch_menu = grant_menu(role, menu_factory, USER_DETAIL_PATH, "PATCH", name="p-user-patch")
        make_user_field_whitelist(role, patch_menu, fields=fields)
        return patch_menu

    @staticmethod
    def _target(dept=None):
        from uuid import uuid4

        user = UserInfo.objects.create_user(username=f"target-{uuid4().hex[:8]}", password="Test@123456")
        if dept is not None:
            user.dept = dept
            user.save(update_fields=["dept"])
        return user

    def test_in_scope_dept_accepted(self, api_client, dept, normal_user, role, menu_factory):
        """管辖子树内的归属调整放行（子部门在管辖范围内）。"""
        sub = DeptInfo.objects.create(name="子部门", code="guard-sub", parent=dept)
        self._setup_manager(dept, normal_user, role, menu_factory)
        target = self._target(dept)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{USER_URL}/{target.pk}", {"dept": str(sub.pk)}, format="json")
        assert resp.status_code == 200
        target.refresh_from_db()
        assert target.dept_id == sub.pk

    def test_out_of_scope_dept_rejected(self, api_client, dept, normal_user, role, menu_factory):
        outside = DeptInfo.objects.create(name="外部部门", code="outside-guard")
        self._setup_manager(dept, normal_user, role, menu_factory)
        target = self._target(dept)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{USER_URL}/{target.pk}", {"dept": str(outside.pk)}, format="json")
        assert resp.status_code == 400
        target.refresh_from_db()
        assert target.dept_id == dept.pk

    def test_roles_outside_pool_rejected(self, api_client, dept, normal_user, role, menu_factory):
        """关系字段授权池：不在可授权面内的角色 → 400 且零副作用（与 empower 同口径）。"""
        other_role = UserRole.objects.create(name="其他角色", code="other-role")
        self._setup_manager(dept, normal_user, role, menu_factory)
        target = self._target(dept)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{USER_URL}/{target.pk}", {"roles": [other_role.pk]}, format="json")
        assert resp.status_code == 400
        assert not target.roles.filter(pk=other_role.pk).exists()

    def test_superuser_any_dept_and_roles_accepted(self, api_client, superuser, dept):
        outside = DeptInfo.objects.create(name="外部部门2", code="outside-guard2")
        other_role = UserRole.objects.create(name="其他角色2", code="other-role2")
        target = self._target()

        api_client.force_authenticate(user=superuser)
        resp = api_client.patch(
            f"{USER_URL}/{target.pk}", {"dept": str(outside.pk), "roles": [other_role.pk]}, format="json"
        )
        assert resp.status_code == 200
        target.refresh_from_db()
        assert target.dept_id == outside.pk
        assert target.roles.filter(pk=other_role.pk).exists()

    def test_clear_semantics(self, api_client, normal_user, dept, role, menu_factory):
        """清空归属：原值非空（挪出管辖）拒绝；原值本空放行。

        非超管 + 全量数据规则把对象可见性拉平，聚焦「清空」这一分支语义。
        """
        normal_user.rules.add(
            DataPermission.objects.create(
                name="all-users-guard",
                rules=[{"table": "identity.userinfo", "field": "id", "type": "value.all", "match": "all", "value": ""}],
            )
        )
        patch_menu = grant_menu(role, menu_factory, USER_DETAIL_PATH, "PATCH", name="p-user-patch")
        make_user_field_whitelist(role, patch_menu, fields=("dept",))
        with_dept = self._target(dept)
        blank = self._target()

        api_client.force_authenticate(user=normal_user)
        resp = api_client.patch(f"{USER_URL}/{with_dept.pk}", {"dept": None}, format="json")
        assert resp.status_code == 400
        with_dept.refresh_from_db()
        assert with_dept.dept_id == dept.pk

        resp = api_client.patch(f"{USER_URL}/{blank.pk}", {"dept": None}, format="json")
        assert resp.status_code == 200
