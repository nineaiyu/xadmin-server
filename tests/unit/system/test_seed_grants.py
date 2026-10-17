# -*- coding: utf-8 -*-
"""声明式授权（``seeds/grants.json``）守护测试。

背景：``loaddata`` 对多对多是覆盖式赋值，角色的菜单授权不能拆进各 app 的 fixture——
否则后装的文件会把先装的授权清零。本模块锁定三条不变量：

1. **声明完整**：种子角色的菜单集合由各层 grants 并集组成，与改造前逐 role 规模一致；
2. **只增不删**：装载只补声明的菜单，不撤销运行期额外授予的菜单，重复执行幂等；
3. **可静态校验**：角色/菜单名写错在 ``--check`` 阶段就报错，而不是装载后静默丢失。
"""

from pathlib import Path

import pytest

from system.services.seeding import (
    PLATFORM,
    GrantDeclaration,
    build_plan,
    discover,
    load_grants,
    merge_grants,
    validate_grants,
)
from system.services.seeding.grants import apply_grants

pytestmark = pytest.mark.django_db

# 改造前 userrole.json 的逐角色菜单数（等价性基线）：改动授权时同步更新
EXPECTED_ROLE_MENUS = {"admin": 255, "test": 75, "default": 29, "demo": 153}


def _plan_grants():
    plan = build_plan(discover())
    declarations, problems = load_grants(plan.grants)
    assert problems == []
    return declarations


class TestDeclarationIntegrity:
    def test_every_seed_role_keeps_its_menu_scale(self):
        """拆分前后每个种子角色的菜单规模必须一致（一次性迁移的验收基线）。"""

        merged = merge_grants(_plan_grants())
        assert {code: len(items) for code, items in merged.items()} == EXPECTED_ROLE_MENUS

    def test_declared_menus_all_exist(self):
        declarations = _plan_grants()
        plan = build_plan(discover())
        menu_names = set()
        for spec in plan.all_specs():
            if spec.model == "system.menu":
                import json

                menu_names.update(row["fields"]["name"] for row in json.loads(spec.path.read_text(encoding="utf-8")))
        role_codes = set(EXPECTED_ROLE_MENUS)
        assert validate_grants(declarations, menu_names=menu_names, role_codes=role_codes) == []

    def test_userrole_seed_no_longer_carries_m2m_menus(self):
        """覆盖式列表必须从 fixture 中消失，否则重灌种子会再次清零其它层的授权。"""

        import json

        from system.services.seeding import platform_file

        rows = json.loads(platform_file("userrole.json").read_text(encoding="utf-8"))
        assert rows
        assert all(not row["fields"].get("menu") for row in rows)

    def test_grants_files_are_declared_by_their_layers(self):
        declarations = _plan_grants()
        assert PLATFORM in {item.app for item in declarations}
        assert "identity" in {item.app for item in declarations}


class TestMerge:
    def test_union_across_layers(self):
        declarations = [
            GrantDeclaration(app=PLATFORM, path=Path("a.json"), roles={"demo": ("a", "b")}),
            GrantDeclaration(app="identity", path=Path("b.json"), roles={"demo": ("b", "c")}),
        ]
        assert merge_grants(declarations) == {"demo": ("a", "b", "c")}


class TestValidation:
    def test_unknown_menu_is_error(self):
        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"demo": ("ghost",)})]
        problems = validate_grants(declarations, menu_names={"real"}, role_codes={"demo"})
        assert [problem.code for problem in problems] == ["GRANT_MENU_UNKNOWN"]

    def test_unknown_role_is_error(self):
        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"ghost": ("real",)})]
        problems = validate_grants(declarations, menu_names={"real"}, role_codes={"demo"})
        assert [problem.code for problem in problems] == ["GRANT_ROLE_UNKNOWN"]

    def test_empty_role_list_skips_validation(self):
        """只装载单个 app 时角色种子可能不在计划内，此时不做角色校验。"""

        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"ghost": ("real",)})]
        assert validate_grants(declarations, menu_names={"real"}, role_codes=set()) == []


@pytest.fixture()
def grant_scene():
    from identity.models import UserRole
    from system.models import Menu, MenuMeta

    role = UserRole.objects.create(code="grant_role", name="授权测试角色")
    granted = Menu.objects.create(
        name="list:GrantTest", path="api/grant/test$", menu_type=2, meta=MenuMeta.objects.create(title="授权")
    )
    extra = Menu.objects.create(
        name="extra:GrantTest", path="api/grant/extra$", menu_type=2, meta=MenuMeta.objects.create(title="额外")
    )
    role.menu.add(extra)
    return role, granted, extra


class TestApply:
    def test_apply_is_additive_and_idempotent(self, grant_scene):
        role, granted, extra = grant_scene
        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"grant_role": (granted.name,)})]

        report = apply_grants(declarations)
        assert report.missing_menus == [] and report.missing_roles == []
        assert set(role.menu.values_list("name", flat=True)) == {granted.name, extra.name}

        apply_grants(declarations)
        assert role.menu.count() == 2

    def test_missing_menu_is_reported_not_fatal(self, grant_scene):
        role, granted, _extra = grant_scene
        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"grant_role": ("ghost",)})]

        report = apply_grants(declarations)
        assert report.missing_menus == ["ghost"]
        assert set(role.menu.values_list("name", flat=True)) == {"extra:GrantTest"}

    def test_missing_role_is_reported_not_fatal(self, grant_scene):
        _role, granted, _extra = grant_scene
        declarations = [GrantDeclaration(app=PLATFORM, path=Path("g.json"), roles={"ghost": (granted.name,)})]
        report = apply_grants(declarations)
        assert report.missing_roles == ["ghost"]


class TestEndToEnd:
    def test_load_init_json_restores_every_role_menu_set(self, seed_creator_user):
        """端到端：装载内置种子后，4 个种子角色的菜单规模与改造前一致。

        这是「授权从覆盖式 m2m 改为声明式并集」的最终验收——用户 role 行不再携带
        m2m，规模只能来自 grants 并集。
        """

        from django.core.management import call_command

        from identity.models import UserRole

        call_command("load_init_json", verbosity=0)

        actual = {
            role.code: role.menu.count()
            for role in UserRole.objects.filter(code__in=EXPECTED_ROLE_MENUS, deleted_at__isnull=True)
        }
        assert actual == EXPECTED_ROLE_MENUS
