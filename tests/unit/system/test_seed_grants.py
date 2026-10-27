# -*- coding: utf-8 -*-
"""声明式授权（``seeds/grants.json``）守护测试。

背景：``loaddata`` 对多对多是覆盖式赋值，角色的菜单授权不能拆进各 app 的 fixture——
否则后装的文件会把先装的授权清零。本模块锁定四条不变量：

1. **声明完整**：种子角色的菜单集合由各层 grants 并集组成，与改造前逐 role 规模一致；
2. **只增不删**：装载只补声明的菜单，不撤销运行期额外授予的菜单，重复执行幂等；
3. **可静态校验**：角色/菜单名写错在 ``--check`` 阶段就报错，而不是装载后静默丢失；
4. **存量不清零**：重灌种子不抹掉库内既有授权——覆盖式 m2m（含空数组）已在装配层
   统一剥离，见 ``test_reseeding_keeps_runtime_granted_menus``。
"""

from pathlib import Path

import pytest

from system.services.seeding import (
    NOTE_CONFLICT,
    NOTE_PRUNE,
    NOTE_STRIP,
    PLATFORM,
    GrantDeclaration,
    LoadReport,
    SeedNote,
    build_plan,
    discover,
    load_grants,
    merge_grants,
    validate_grants,
)
from system.services.seeding.grants import apply_grants

pytestmark = pytest.mark.django_db

# 改造前 userrole.json 的逐角色菜单数（等价性基线）：改动授权时同步更新
# （2026-10 审计：test 角色补齐「页面 ⇒ 只读子权限点」缺口 75 → 80；
#  2026-10 演示角色改为 allow-list：153 → 63，身份治理/系统配置/日志/文件等整页摘除；
#  2026-10 字段/设置页改只读台账：移除 16 个写/删除/导入权限点，admin 255 → 251；
#  2026-10 聊天群权限：admin/default/test 补 createGroup/members/rename/leave，demo 补 createGroup/rename/leave
#  2026-10 开放平台设置页：admin 补页面 + SCIM/第三方登录 8 个权限点，268 → 279）
#  2026-10 设置页归类：admin 补「通知渠道」「账号安全」两个页面，290 → 292）
EXPECTED_ROLE_MENUS = {"admin": 297, "test": 87, "default": 33, "demo": 67}


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
        """覆盖式列表必须从 fixture 中消失，否则重灌种子会再次清零其它层的授权。

        空数组同样致命：``loaddata`` 见到 ``menu`` 键就执行 ``set([])``，与携带
        一整串菜单名的后果相同。真正的守护在 ``test_reseeding_keeps_runtime_granted_menus``
        （装配层已统一剥离 m2m），此处仅锁定源文件口径。
        """

        import json

        from system.services.seeding import platform_file

        rows = json.loads(platform_file("userrole.json").read_text(encoding="utf-8"))
        assert rows
        assert all(not row["fields"].get("menu") for row in rows)

    def test_strip_m2m_fields_removes_empty_lists_too(self):
        """装配层必须连空数组一起剥掉——``set([])`` 与 ``set([...])`` 一样是覆盖。"""

        from system.services.seeding import strip_m2m_fields

        rows = {
            "identity.userrole": [
                {"pk": "r1", "fields": {"code": "default", "menu": []}},
                {"pk": "r2", "fields": {"code": "admin", "menu": ["list:Menu"]}},
            ]
        }
        stripped, notes = strip_m2m_fields(rows)
        assert all("menu" not in row["fields"] for row in stripped["identity.userrole"])
        # 授权字段名与角色标识不受影响
        assert [row["fields"]["code"] for row in stripped["identity.userrole"]] == ["default", "admin"]
        assert len(notes) == 2
        # 剥离是策略行为（行照常导入），不能标记成「冲突」
        assert {note.kind for note in notes} == {NOTE_STRIP}

    def test_strip_m2m_fields_leaves_attribute_m2m_alone(self):
        """属性型 m2m（菜单→模型）是功能契约，覆盖语义正是所需，不能被误剥。

        ``menu.model`` / ``fieldpermission.field`` 决定字段白名单的取值面，种子值即
        期望值；只有「角色被授予哪些菜单」是客户资产，需要并集语义。
        """

        from system.services.seeding import strip_m2m_fields

        rows = {
            "system.menu": [{"pk": "m1", "fields": {"name": "list:X", "model": ["identity.userinfo"]}}],
            "system.fieldpermission": [{"pk": "f1", "fields": {"field": ["a", "b"]}}],
        }
        stripped, notes = strip_m2m_fields(rows)
        assert stripped["system.menu"][0]["fields"]["model"] == ["identity.userinfo"]
        assert stripped["system.fieldpermission"][0]["fields"]["field"] == ["a", "b"]
        assert notes == []

    def test_grants_files_are_declared_by_their_layers(self):
        declarations = _plan_grants()
        assert PLATFORM in {item.app for item in declarations}
        assert "identity" in {item.app for item in declarations}


class TestLoadReportNotes:
    """装配说明的类别分流：只有真冲突计入「跳过」，剥离不冒充冲突。"""

    def test_notes_are_partitioned_by_kind(self):
        report = LoadReport(
            notes=[
                SeedNote("跳过 x：自然键被占用", NOTE_CONFLICT),
                SeedNote("x：移除 1 个已跳过主键", NOTE_PRUNE),
                SeedNote("x：剥离多对多字段 menu", NOTE_STRIP),
            ]
        )
        assert [str(note) for note in report.conflict_notes] == ["跳过 x：自然键被占用"]
        assert [str(note) for note in report.prune_notes] == ["x：移除 1 个已跳过主键"]
        assert [str(note) for note in report.strip_notes] == ["x：剥离多对多字段 menu"]

    def test_untyped_note_defaults_to_conflict(self):
        """未标类别的普通字符串按冲突处理——宁可多报，不可把真问题降级。"""

        report = LoadReport(notes=["跳过 x：未标类别"])
        assert report.conflict_notes == ["跳过 x：未标类别"]
        assert report.prune_notes == []
        assert report.strip_notes == []


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

    def test_reseeding_keeps_runtime_granted_menus(self, seed_creator_user):
        """存量守护：重灌种子不得清零运行期授予的菜单。

        缺陷现场（4.2.5 → 5.0.1 升级实测）：``userrole.json`` 给种子角色带
        ``"menu": []``，``loaddata`` 的 m2m 覆盖语义对空数组同样执行 ``set([])``，
        把客户「默认权限」角色人工授予的二开菜单整体抹掉，随后 ``apply_grants``
        只加回 grants 声明的那几项——数据静默丢失且无任何告警。
        """

        from django.core.management import call_command

        from identity.models import UserRole
        from system.models import Menu, MenuMeta

        call_command("load_init_json", verbosity=0)
        role = UserRole.objects.get(code="default")
        baseline = role.menu.count()
        assert baseline == EXPECTED_ROLE_MENUS["default"]

        # 运行期额外授予一个菜单（模拟客户的人工授权 / 二开模块权限点）
        extra = Menu.objects.create(
            name="list:RuntimeGranted",
            path="api/runtime/granted$",
            menu_type=2,
            meta=MenuMeta.objects.create(title="运行期"),
        )
        role.menu.add(extra)

        call_command("load_init_json", verbosity=0)

        role.refresh_from_db()
        assert extra.name in set(role.menu.values_list("name", flat=True))
        assert role.menu.count() == baseline + 1
