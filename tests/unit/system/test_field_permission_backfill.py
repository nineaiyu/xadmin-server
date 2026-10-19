# -*- coding: utf-8 -*-
"""字段权限缺口补齐（permission_sync.fill_field_permissions）守护。

字段权限为 fail-closed 的零字段口径（见 common/core/serializers.py 的
get_allow_fields）：角色在某权限点上没有非空白名单时，该接口输出空对象，
前端列表/详情整块空白。本模块锁定补齐语义：

1. 缺口组合（无行 / 行内字段为空）补齐为该权限点绑定模型的全字段；
2. 已有非空白名单的组合不改写（管理员在角色页的收敛配置不被覆盖）；
3. 只处理有模型绑定的权限点；无绑定的动作类权限点（chat/flower 等）不参与；
4. 幂等：重复执行第二次补齐数为 0；
5. 命令入口 --fix-field-permissions 与审计口径同源。
"""

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command

from identity.models import UserRole
from system.models import FieldPermission, Menu, MenuMeta, ModelLabelField
from system.services.permission_sync import (
    apply_binding_fixes,
    audit_field_permissions,
    fill_field_permissions,
    model_field_nodes,
    plan_binding_fixes,
)

pytestmark = pytest.mark.django_db

ROLE = ModelLabelField.FieldChoices.ROLE


@pytest.fixture
def field_tree(db: Any) -> Any:
    """角色字段树：一个模型根 + 两个字段节点。"""
    root = ModelLabelField.objects.create(name="demo.item", label="示例对象", field_type=ROLE)
    ModelLabelField.objects.create(name="title", label="标题", parent=root, field_type=ROLE)
    ModelLabelField.objects.create(name="amount", label="金额", parent=root, field_type=ROLE)
    return root


@pytest.fixture
def bound_menu(db: Any, field_tree: Any) -> Any:
    """绑定模型的列表权限点（CRUD 类：需要字段白名单）。"""
    meta = MenuMeta.objects.create(title="示例列表")
    menu = Menu.objects.create(
        name="list:DemoItem",
        path="api/demo/item$",
        menu_type=Menu.MenuChoices.PERMISSION,
        method="GET",
        meta=meta,
    )
    menu.model.set([field_tree])
    return menu


@pytest.fixture
def demo_role(db: Any, bound_menu: Any) -> Any:
    role = UserRole.objects.create(name="补齐演示角色", code="demo_backfill", is_active=True)
    role.menu.set([bound_menu])
    return role


def test_model_field_nodes_groups_fields_by_binding(demo_role: Any, bound_menu: Any) -> None:
    """字段节点按「权限点绑定模型的角色字段树子节点」收集。"""
    grouped = model_field_nodes([bound_menu])
    assert set(node.name for node in grouped[bound_menu.pk]) == {"title", "amount"}


def test_audit_and_nodes_queries_do_not_scale_with_menu_count(field_tree: Any) -> None:
    """绑定模型读取必须批量：查询数不得随菜单数线性增长（N+1 回归护栏）。

    种子收尾对全部权限点做一轮全量审计（``load_init_json`` 收尾 + 内置角色补齐），
    逐菜单读 ``menu.model`` 会在数百个权限点上放大成数百条小往返。满载机器上一条
    小查询可被拖到秒级，那份放大就是尾部假死的燃料——这里锁住「不随规模线性增长」。
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def _build(count: int, tag: str) -> tuple[Any, list[Any]]:
        role = UserRole.objects.create(name=f"护栏角色 {tag}", code=f"guard_{tag}", is_active=True)
        menus = []
        for index in range(count):
            meta = MenuMeta.objects.create(title=f"批量菜单 {tag}{index}")
            menu = Menu.objects.create(
                name=f"list:Bulk{tag}{index}",
                path=f"api/bulk/{tag}/{index}$",
                menu_type=Menu.MenuChoices.PERMISSION,
                method="GET",
                meta=meta,
            )
            menu.model.set([field_tree])
            menus.append(menu)
        role.menu.set(menus)
        return role, menus

    role_few, few = _build(5, "few")
    role_many, many = _build(40, "many")

    with CaptureQueriesContext(connection) as ctx_few:
        audit_field_permissions(roles=[role_few])
        model_field_nodes(few)
    with CaptureQueriesContext(connection) as ctx_many:
        audit_field_permissions(roles=[role_many])
        model_field_nodes(many)

    assert len(ctx_many.captured_queries) <= len(ctx_few.captured_queries) + 3, (
        "审计/字段节点读取的查询数随菜单数增长（N+1 回归）："
        f"{len(ctx_few.captured_queries)} → {len(ctx_many.captured_queries)}"
    )


def test_fill_writes_do_not_scale_with_menu_count(field_tree: Any) -> None:
    """补齐的写入必须批量：查询数不得随（角色, 权限点）组合数线性增长。

    写入侧曾经的形态是逐组合 ``get_or_create`` + ``.set()``——后者每次
    DELETE+INSERT+SELECT 三条往返，角色×菜单数百时即数千条小查询，
    与读取侧 N+1 叠加后把种子收尾拖到分钟级（全量 pytest 尾部假死的直接燃料）。
    同一模型的权限点共享一份白名单，因此按「字段集」分组后写入应基本恒定。
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def _build(count: int, tag: str) -> Any:
        role = UserRole.objects.create(name=f"写入护栏 {tag}", code=f"wguard_{tag}", is_active=True)
        menus = []
        for index in range(count):
            meta = MenuMeta.objects.create(title=f"写入门菜单 {tag}{index}")
            menu = Menu.objects.create(
                name=f"list:WBulk{tag}{index}",
                path=f"api/wbulk/{tag}/{index}$",
                menu_type=Menu.MenuChoices.PERMISSION,
                method="GET",
                meta=meta,
            )
            menu.model.set([field_tree])
            menus.append(menu)
        role.menu.set(menus)
        return role

    role_few = _build(5, "few")
    role_many = _build(40, "many")

    # 首次补齐允许随缺口线性（建行是固有成本，FieldPermission 的 id 由 save
    # 路径拼装、bulk_create 拿不到）；这里锁的是**幂等路径**——种子收尾每轮都走
    # 第二次，缺口为 0 时查询数必须恒定，否则全量跑批会逐轮线性放大
    assert fill_field_permissions(roles=[role_few])
    assert fill_field_permissions(roles=[role_many])

    with CaptureQueriesContext(connection) as ctx_few:
        assert fill_field_permissions(roles=[role_few]) == []
    with CaptureQueriesContext(connection) as ctx_many:
        assert fill_field_permissions(roles=[role_many]) == []

    assert len(ctx_many.captured_queries) <= len(ctx_few.captured_queries) + 3, (
        "补齐写入的查询数随组合数增长（写入 N+1 回归）："
        f"{len(ctx_few.captured_queries)} → {len(ctx_many.captured_queries)}"
    )
    # 语义不变：每个组合都真的拿到了全字段白名单
    filled_menus = set(role_many.menu.values_list("pk", flat=True))
    for permission in FieldPermission.objects.filter(role=role_many, menu_id__in=filled_menus):
        assert set(permission.field.values_list("name", flat=True)) == {"title", "amount"}


def test_fill_creates_whitelist_for_missing_pair(demo_role: Any, bound_menu: Any) -> None:
    assert audit_field_permissions(roles=[demo_role]) == [(demo_role, bound_menu)]

    filled = fill_field_permissions(roles=[demo_role])

    assert len(filled) == 1
    permission = FieldPermission.objects.get(role=demo_role, menu=bound_menu)
    assert set(permission.field.values_list("name", flat=True)) == {"title", "amount"}


def test_fill_treats_empty_row_as_gap(demo_role: Any, bound_menu: Any) -> None:
    """行存在但字段为空与「没有行」等价（都产出空白名单），一并补齐。"""
    FieldPermission.objects.create(role=demo_role, menu=bound_menu)

    filled = fill_field_permissions(roles=[demo_role])

    assert len(filled) == 1
    permission = FieldPermission.objects.get(role=demo_role, menu=bound_menu)
    assert permission.field.count() == 2


def test_fill_keeps_existing_nonempty_whitelist(demo_role: Any, bound_menu: Any, field_tree: Any) -> None:
    """已有非空白名单（管理员收敛过的字段子集）不被覆盖。"""
    permission = FieldPermission.objects.create(role=demo_role, menu=bound_menu)
    title = ModelLabelField.objects.get(name="title", parent=field_tree)
    permission.field.set([title])

    assert fill_field_permissions(roles=[demo_role]) == []
    assert set(permission.field.values_list("name", flat=True)) == {"title"}


def test_audit_flags_whitelist_disjoint_from_binding(demo_role: Any, bound_menu: Any, field_tree: Any) -> None:
    """白名单与绑定模型完全脱节（数据源模型迁移残留）等价于空白名单，计入缺口。

    运行时按「模型标签 + 字段名」匹配（get_user_field_queryset），旧模型字段
    不会命中任何当前模型，页面输出空对象；不识别的话脱节配置会把缺口永久掩盖。
    """
    legacy_root = ModelLabelField.objects.create(name="demo.legacy", label="旧模型", field_type=ROLE)
    legacy = ModelLabelField.objects.create(name="legacy_field", label="旧字段", parent=legacy_root, field_type=ROLE)
    permission = FieldPermission.objects.create(role=demo_role, menu=bound_menu)
    permission.field.set([legacy])

    assert audit_field_permissions(roles=[demo_role]) == [(demo_role, bound_menu)]

    filled = fill_field_permissions(roles=[demo_role])

    assert len(filled) == 1
    # 脱节白名单被重写为绑定模型全字段（旧模型字段清除）
    assert set(permission.field.values_list("name", flat=True)) == {"title", "amount"}


def test_fill_keeps_partially_valid_whitelist(demo_role: Any, bound_menu: Any, field_tree: Any) -> None:
    """白名单含绑定模型字段（哪怕同时残留旧模型字段）视为有效配置，不覆盖。"""
    permission = FieldPermission.objects.create(role=demo_role, menu=bound_menu)
    title = ModelLabelField.objects.get(name="title", parent=field_tree)
    legacy_root = ModelLabelField.objects.create(name="demo.legacy", label="旧模型", field_type=ROLE)
    legacy = ModelLabelField.objects.create(name="legacy_field", label="旧字段", parent=legacy_root, field_type=ROLE)
    permission.field.set([title, legacy])

    assert audit_field_permissions(roles=[demo_role]) == []
    assert fill_field_permissions(roles=[demo_role]) == []
    assert set(permission.field.values_list("name", flat=True)) == {"title", "legacy_field"}


def test_fill_skips_menu_without_model_binding(demo_role: Any) -> None:
    """无模型绑定的权限点（动作类/自定义视图）不进审计面，也不补字段。"""
    meta = MenuMeta.objects.create(title="推送动作")
    action_menu = Menu.objects.create(
        name="push:DemoItem",
        path="api/demo/item/(?P<pk>[^/.]+)/push$",
        menu_type=Menu.MenuChoices.PERMISSION,
        method="POST",
        meta=meta,
    )
    demo_role.menu.add(action_menu)

    assert all(menu.pk != action_menu.pk for _role, menu in audit_field_permissions(roles=[demo_role]))
    fill_field_permissions(roles=[demo_role])
    assert not FieldPermission.objects.filter(role=demo_role, menu=action_menu).exists()


def test_fill_is_idempotent(demo_role: Any) -> None:
    assert len(fill_field_permissions(roles=[demo_role])) == 1
    assert fill_field_permissions(roles=[demo_role]) == []


def test_audit_roles_filter_scopes_result(db: Any, demo_role: Any, bound_menu: Any) -> None:
    """roles 过滤限定审计面（单角色补齐场景不扫描其他角色）。"""
    other = UserRole.objects.create(name="其他角色", code="demo_other", is_active=True)
    other.menu.set([bound_menu])

    assert [role.pk for role, _menu in audit_field_permissions(roles=[demo_role])] == [demo_role.pk]

    all_pairs = audit_field_permissions()
    assert {role.pk for role, _menu in all_pairs} == {demo_role.pk, other.pk}


def test_command_fix_field_permissions_via_dedicated_entry(demo_role: Any, bound_menu: Any) -> None:
    """命令独立入口：--skip-permissions --fix-field-permissions 也可补齐（免全量路由扫描）。"""
    call_command("sync_menu_permissions", "--skip-permissions", "--skip-fields", "--fix-field-permissions")

    permission = FieldPermission.objects.get(role=demo_role, menu=bound_menu)
    assert permission.field.count() == 2


def test_command_dry_run_does_not_write(demo_role: Any, bound_menu: Any) -> None:
    """dry-run 只报告不落库（允许与 --fix-field-permissions 组合做预演）。"""
    call_command(
        "sync_menu_permissions",
        "--skip-permissions",
        "--skip-fields",
        "--fix-field-permissions",
        "--dry-run",
    )

    assert not FieldPermission.objects.filter(role=demo_role, menu=bound_menu).exists()


class TestBindingShrink:
    """模型绑定校正：视图数据源迁移后收缩陈旧绑定（旧绑定会掩盖白名单脱节）。"""

    @staticmethod
    def _route(path: str, view_cls: Any) -> Any:
        return SimpleNamespace(url=path, actions={"get": "list"}, view_cls=view_cls)

    @staticmethod
    def _perm_menu(path: str, roots: Any) -> Any:
        meta = MenuMeta.objects.create(title="在线用户")
        menu = Menu.objects.create(
            name="list:SystemUserOnline",
            path=f"{path}$",
            menu_type=Menu.MenuChoices.PERMISSION,
            method="GET",
            meta=meta,
        )
        menu.model.set(roots)
        return menu

    @staticmethod
    def _fake_view(model: Any) -> Any:
        class FakeView:
            queryset = model.objects.all()

        return FakeView

    def test_shrinks_binding_outside_view_models(self) -> None:
        """绑定含视图已不再关联的模型（迁移残留）时收缩到当前模型。"""
        from identity.models import UserSession

        current_root = ModelLabelField.objects.create(name="identity.usersession", label="会话", field_type=ROLE)
        stale_root = ModelLabelField.objects.create(name="audit.userloginlog", label="登录日志", field_type=ROLE)
        menu = self._perm_menu("api/identity/online", [current_root, stale_root])
        route = self._route("api/identity/online$", self._fake_view(UserSession))

        fixes = plan_binding_fixes([route], [menu])

        assert len(fixes) == 1
        assert fixes[0].mode == "shrink"
        assert fixes[0].expected == {current_root.pk}
        apply_binding_fixes(fixes)
        assert set(menu.model.values_list("pk", flat=True)) == {current_root.pk}

    def test_adjust_adds_missing_and_shrinks_stale(self) -> None:
        """同时存在缺失与陈旧绑定时一次校正到位（补新 + 收缩旧）。"""
        from identity.models import UserSession

        current_root = ModelLabelField.objects.create(name="identity.usersession", label="会话", field_type=ROLE)
        stale_root = ModelLabelField.objects.create(name="audit.userloginlog", label="登录日志", field_type=ROLE)
        menu = self._perm_menu("api/identity/online", [stale_root])
        route = self._route("api/identity/online$", self._fake_view(UserSession))

        fixes = plan_binding_fixes([route], [menu])

        assert len(fixes) == 1
        assert fixes[0].mode == "adjust"
        apply_binding_fixes(fixes)
        assert set(menu.model.values_list("pk", flat=True)) == {current_root.pk}

    def test_view_without_model_keeps_binding(self) -> None:
        """视图无模型（APIView / 自定义端点）时不判定陈旧，手工绑定保留。"""
        root = ModelLabelField.objects.create(name="demo.manual", label="手工", field_type=ROLE)
        menu = self._perm_menu("api/demo/manual", [root])
        route = self._route("api/demo/manual$", SimpleNamespace(queryset=None))

        fixes = plan_binding_fixes([route], [menu])

        assert fixes == []

    def test_fill_after_binding_shrink_rebuilds_whitelist(
        self, demo_role: Any, bound_menu: Any, field_tree: Any
    ) -> None:
        """陈旧绑定会掩盖白名单脱节；收缩绑定后同一组合被识别为缺口并重写。

        命令内的执行顺序即为此语义：先 apply_binding_fixes 收缩绑定，再补齐。
        """
        stale_root = ModelLabelField.objects.create(name="demo.legacy", label="旧模型", field_type=ROLE)
        stale = ModelLabelField.objects.create(name="legacy_field", label="旧字段", parent=stale_root, field_type=ROLE)
        bound_menu.model.add(stale_root)
        permission = FieldPermission.objects.create(role=demo_role, menu=bound_menu)
        permission.field.set([stale])

        # 陈旧绑定仍在：stale 命中绑定模型，不判缺口（正是需要先收缩的原因）
        assert fill_field_permissions(roles=[demo_role]) == []

        # 等价于 sync_menu_permissions 的绑定校正
        bound_menu.model.set([field_tree])

        filled = fill_field_permissions(roles=[demo_role])

        assert len(filled) == 1
        assert set(permission.field.values_list("name", flat=True)) == {"title", "amount"}
