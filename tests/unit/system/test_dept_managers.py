#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""部门管理员（ADR-077）：manager 规则解析、任命装配与宽授权巡检单测。"""

import pytest

from common.core.data_scope import resolve_rule
from system.models import DataPermission, DeptInfo, DeptManagerAssignment, UserInfo
from system.utils.dept_managers import (
    DEPT_MANAGER_ROLE_CODE,
    DEPT_MANAGER_RULE_SPECS,
    assign_dept_managers,
    ensure_preset_rules,
)
from system.utils.permission_sync import audit_wide_manager_grants

pytestmark = pytest.mark.django_db


def _rule(f_type):
    return {"table": "system.userinfo", "field": "id", "type": f_type, "value": "*", "match": "in"}


class TestManagerRuleResolution:
    """value.manager.* 规则解析：按任命关系注入部门子树 / 成员集合。"""

    def test_manager_departments_expands_subtree(self, dept, normal_user):
        child = DeptInfo.objects.create(name="子部门", code="manager-sub", parent=dept)
        DeptManagerAssignment.objects.create(dept=dept, user=normal_user)

        cond = resolve_rule(_rule("value.manager.dept.ids"), normal_user)
        assert cond["match"] == "in"
        pks = {str(pk) for pk in cond["value"]}
        assert {str(dept.pk), str(child.pk)} <= pks

    def test_manager_departments_ignores_inactive_dept(self, dept, normal_user):
        DeptManagerAssignment.objects.create(dept=dept, user=normal_user)
        DeptInfo.objects.filter(pk=dept.pk).update(is_active=False)

        assert resolve_rule(_rule("value.manager.dept.ids"), normal_user)["value"] == []

    def test_manager_users_returns_managed_members(self, dept, normal_user):
        DeptManagerAssignment.objects.create(dept=dept, user=normal_user)
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        member = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        member.dept = dept
        member.save(update_fields=["dept"])

        cond = resolve_rule(_rule("value.manager.user.ids"), normal_user)
        pks = {str(pk) for pk in cond["value"]}
        assert {str(normal_user.pk), str(member.pk)} <= pks

    def test_non_manager_resolves_to_empty(self, dept, normal_user):
        assert resolve_rule(_rule("value.manager.dept.ids"), normal_user)["value"] == []
        assert resolve_rule(_rule("value.manager.user.ids"), normal_user)["value"] == []


class TestPresetRules:
    """预置数据权限规则维护：幂等创建 + 内容与声明一致。"""

    def test_ensure_preset_rules_idempotent_and_scoped(self):
        first = ensure_preset_rules()
        assert len(first) == len(DEPT_MANAGER_RULE_SPECS)
        second = ensure_preset_rules()
        assert {dp.pk for dp in first} == {dp.pk for dp in second}

        by_name = {dp.name: dp for dp in first}
        assert "value.manager.user.ids" in str(by_name["部门管理-本部门及下级成员"].rules)
        assert "value.manager.dept.ids" in str(by_name["部门管理-本部门及下级"].rules)


class TestAssignAssembly:
    """任命装配：through 行 + 预置角色成员 + 用户级预置规则（幂等、可回收）。"""

    def test_assign_grants_role_and_rules(self, dept, normal_user, superuser):
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)

        assert DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).exists()
        assert normal_user.roles.filter(code=DEPT_MANAGER_ROLE_CODE).exists()
        assert normal_user.rules.filter(name__startswith="部门管理-").count() == len(DEPT_MANAGER_RULE_SPECS)

    def test_assign_idempotent(self, dept, normal_user, superuser):
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assert DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).count() == 1

    def test_inactive_user_skipped(self, dept, normal_user, superuser):
        normal_user.is_active = False
        normal_user.save(update_fields=["is_active"])

        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assert not DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).exists()

    def test_remove_recycles_role_and_rules(self, dept, normal_user, superuser):
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assign_dept_managers(dept, add_pks=[], remove_pks=[normal_user.pk], operator=superuser)

        assert not DeptManagerAssignment.objects.filter(dept=dept, user=normal_user).exists()
        assert not normal_user.roles.filter(code=DEPT_MANAGER_ROLE_CODE).exists()
        assert normal_user.rules.filter(name__startswith="部门管理-").count() == 0

    def test_remove_keeps_assembly_when_managing_other_dept(self, dept, normal_user, superuser):
        other = DeptInfo.objects.create(name="市场部", code="market")
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assign_dept_managers(other, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)

        # 解除一个部门的任命但仍在管理另一个部门 → 角色与规则保留
        assign_dept_managers(dept, add_pks=[], remove_pks=[normal_user.pk], operator=superuser)
        assert normal_user.roles.filter(code=DEPT_MANAGER_ROLE_CODE).exists()
        assert normal_user.rules.filter(name__startswith="部门管理-").count() == len(DEPT_MANAGER_RULE_SPECS)


class TestAuditWideManagerGrants:
    """巡检：部门管理员持宽规则（value.all）显式暴露。"""

    def test_flags_manager_with_all_rule(self, dept, normal_user, superuser):
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        wide = DataPermission.objects.create(
            name="测试-全量",
            rules=[{"table": "*", "field": "id", "type": "value.all", "match": "all", "value": ""}],
        )
        normal_user.rules.add(wide)

        findings = audit_wide_manager_grants()
        assert any(user.pk == normal_user.pk and name == "测试-全量" for user, name in findings)

    def test_no_finding_without_wide_rule(self, dept, normal_user, superuser):
        assign_dept_managers(dept, add_pks=[normal_user.pk], remove_pks=[], operator=superuser)
        assert audit_wide_manager_grants() == []
