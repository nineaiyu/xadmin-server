# -*- coding: utf-8 -*-
"""开箱模板命令 seed_demo_org：组织 / 角色四层权限 / 场景模板。

守护三件事：
1. 命令一次建齐示例组织、预置角色（菜单 + 权限点 + 字段权限 + 数据权限）与场景模板；
2. 重复执行幂等（不产生重复部门/账号/角色）；
3. --reset 可清理后重建（用户软删持有部门外键，清理须硬删后再删部门）。
"""

import pytest
from django.core.management import call_command

from system.models import (
    DataPermission,
    DeptInfo,
    Menu,
    MenuMeta,
    ModelLabelField,
    UserInfo,
    UserRole,
)
from system.models.approval import ApprovalFlow
from system.models.dform import DynamicForm

pytestmark = pytest.mark.django_db

# 真实环境由 load_init_json 灌入菜单与字段树；测试库为空，这里按命令依赖的最小集构造
PAGE_PATHS = [
    "/form-collection/my/index",
    "/system/approval/instance/index",
    "/system/approval/index",
    "/user/notice/index",
    "/system/leave/index",
]


@pytest.fixture
def menus(db):
    pages = []
    for path in PAGE_PATHS:
        meta = MenuMeta.objects.create(title=path)
        page = Menu.objects.create(name=path, path=path, menu_type=Menu.MenuChoices.MENU, meta=meta)
        Menu.objects.create(
            name=f"perm{path}",
            path=f"api{path}$",
            menu_type=Menu.MenuChoices.PERMISSION,
            parent=page,
            method="GET",
            meta=MenuMeta.objects.create(title=f"perm{path}"),
        )
        pages.append(page)
    return pages


@pytest.fixture
def field_trees(db):
    for model_name in ("system.approvalflow", "system.approvalinstance", "system.leave"):
        root = ModelLabelField.objects.create(name=model_name, label=model_name, field_type=0)
        ModelLabelField.objects.create(name="id", label="ID", parent=root, field_type=1)


def test_seed_creates_org_roles_and_scenes(menus, field_trees):
    call_command("seed_demo_org")

    assert DeptInfo.objects.filter(code__in=["demo_rd", "demo_fin"]).count() == 2
    assert UserInfo.objects.filter(username__in=["demo_lead", "demo_staff", "demo_fin"]).count() == 3
    assert UserRole.objects.filter(code__in=["demo_staff_role", "demo_leader_role"]).count() == 2

    # 部门主管链路：主管可解析（leader 指派的前提）
    rd = DeptInfo.objects.get(code="demo_rd")
    assert rd.leader is not None and rd.leader.username == "demo_lead"

    # 四层权限：菜单授权 + 字段权限 + 数据权限
    staff_role = UserRole.objects.get(code="demo_staff_role")
    assert staff_role.menu.exists()
    assert staff_role.fieldpermission_set.exists()
    staff = UserInfo.objects.get(username="demo_staff")
    assert staff.rules.exists()
    assert DataPermission.objects.filter(name__startswith="示例-").exists()

    # 场景模板：报销流程（含条件节点）+ 绑定流程的入职登记表
    assert ApprovalFlow.objects.filter(code="demo_expense").exists()
    assert ApprovalFlow.objects.filter(code="demo_onboarding").exists()
    onboarding = DynamicForm.objects.filter(name="示例-入职登记表").first()
    assert onboarding is not None and onboarding.approval_flow_id is not None
    # 模板表单含新控件（附件 / 日期范围 / 明细子表）
    types = {field["type"] for field in onboarding.schema.get("fields", [])}
    assert {"upload", "daterange", "table"} & types


def test_seed_is_idempotent():
    call_command("seed_demo_org")
    dept_count = DeptInfo.objects.count()
    user_count = UserInfo.objects.count()
    role_count = UserRole.objects.count()

    call_command("seed_demo_org")
    assert DeptInfo.objects.count() == dept_count
    assert UserInfo.objects.count() == user_count
    assert UserRole.objects.count() == role_count


def test_seed_reset_rebuilds_cleanly():
    call_command("seed_demo_org")
    call_command("seed_demo_org", "--reset")
    assert DeptInfo.objects.filter(code="demo_rd").count() == 1
    assert UserInfo.objects.filter(username="demo_staff").count() == 1
    assert DynamicForm.objects.filter(name="示例-入职登记表").count() == 1


def test_seed_skips_missing_model_field_tree():
    """字段树未同步（全新库 migrate 前）时不阻塞命令执行。"""
    ModelLabelField.objects.all().delete()
    call_command("seed_demo_org")
    assert UserInfo.objects.filter(username="demo_staff").exists()
