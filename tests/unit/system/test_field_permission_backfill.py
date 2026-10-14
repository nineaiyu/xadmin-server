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

from typing import Any

import pytest
from django.core.management import call_command

from identity.models import UserRole
from system.models import FieldPermission, Menu, MenuMeta, ModelLabelField
from system.services.permission_sync import (
    audit_field_permissions,
    fill_field_permissions,
    model_field_nodes,
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
