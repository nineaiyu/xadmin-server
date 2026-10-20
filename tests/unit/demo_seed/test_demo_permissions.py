# -*- coding: utf-8 -*-
"""演示角色 allow-list 与演示租户数据权限种子的静态守护。

这些用例不依赖数据库：直接校验种子/白名单的静态契约，防止后续新增权限点
或调整部门树时把演示账号重新放大成「管理员减几个权限」。
"""

import json
from pathlib import Path

import pytest

from demo_seed.management.commands.seed_demo_admin import (
    CONFIG_MODELS,
    DENY_PERMISSIONS,
    DENY_PREFIXES,
    WRITE_ACTIONS,
)
from demo_seed.permissions import (
    DEMO_ALLOWED_MENU_NAMES,
    DEMO_ALLOWED_PERMISSION_NAMES,
    DEMO_PAGE_NAMES,
    DEMO_PAGE_PATHS,
)

SERVER_ROOT = Path(__file__).resolve().parents[3]
LOADJSON = SERVER_ROOT / "loadjson"


def _grant_files() -> list[Path]:
    return sorted(SERVER_ROOT.glob("*/seeds/grants.json"))


def test_allow_list_has_no_destructive_entries():
    """allow-list 里不能出现删除/导入/密码重置/配置写入类权限。"""

    for name in DEMO_ALLOWED_PERMISSION_NAMES:
        assert not name.startswith(DENY_PREFIXES), name
        assert name not in DENY_PERMISSIONS, name
        action, _, target = name.partition(":")
        assert not (target in CONFIG_MODELS and action in WRITE_ACTIONS), name


def test_grants_demo_lists_are_subset_of_allow_list():
    """各 app 的 grants.json 中 demo 角色不得声明 allow-list 之外的菜单。"""

    for path in _grant_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        demo = set(data.get("roles", {}).get("demo", []))
        extra = demo - DEMO_ALLOWED_MENU_NAMES
        assert not extra, f"{path} 演示角色越界授权：{sorted(extra)}"


def test_demo_page_paths_are_unique():
    assert len(DEMO_PAGE_PATHS) == len(set(DEMO_PAGE_PATHS))
    assert len(DEMO_PAGE_NAMES) == len(DEMO_PAGE_PATHS)


def test_allow_listed_menus_exist_in_seed_files():
    """除动态创建的 DemoBook 外，allow-list 菜单名必须能在种子菜单中找到。"""

    names: set[str] = set()
    for path in [*SERVER_ROOT.glob("*/seeds/menu.json"), LOADJSON / "menu.json"]:
        if not path.is_file():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            name = row.get("fields", {}).get("name")
            if name:
                names.add(name)
    # DemoBook 页面与其权限点由 seed_demo_book 动态创建（不在静态 seed 文件里）
    missing = {
        name for name in DEMO_ALLOWED_MENU_NAMES if name != "DemoBook" and not name.endswith(":DemoBook")
    } - names
    assert not missing, f"allow-list 引用了不存在的菜单：{sorted(missing)}"


def test_demo_dept_is_isolated_top_level_tenant():
    """演示部门必须是顶层，且不再挂到 xAdmin 管理团队（避免继承全部数据）。"""

    rows = json.loads((LOADJSON / "deptinfo.json").read_text(encoding="utf-8"))
    by_code = {row["fields"]["code"]: row for row in rows}
    demo = by_code["demo"]["fields"]
    assert demo["parent"] is None
    assert by_code["test"]["fields"]["parent"] is None
    assert by_code["default"]["fields"]["parent"] is None
    # 演示部门显式挂三个演示租户数据权限
    assert {
        "31ea340e-0c81-4d62-a141-90efc61f17e6",
        "86dc227d-6676-4c22-a28d-c98b77d0def6",
        "7218022d-0141-4df4-a134-c2d0f2676dc9",
        "9678b554-0659-4ca1-a9a0-c17d283cd774",
    } <= set(demo["rules"])


def test_demo_scope_permissions_cover_userinfo_and_business_rows():
    """演示数据权限必须同时约束 UserInfo 的 dept 字段与业务表的 dept_belong。"""

    rows = json.loads((LOADJSON / "datapermission.json").read_text(encoding="utf-8"))
    by_name = {row["fields"]["name"]: row["fields"] for row in rows}

    user_scope = by_name["演示部门用户范围"]["rules"]
    assert {(rule["table"], rule["field"], rule["type"]) for rule in user_scope} == {
        ("identity.userinfo", "dept", "value.user.dept.ids"),
        ("identity.userinfo", "dept_belong", "value.user.dept.ids"),
    }

    write_scope = {(rule["table"], rule["field"], rule["type"]) for rule in by_name["演示写入数据范围"]["rules"]}
    for table in (
        "approval.approvalinstance",
        "approval.approvalrequest",
        "approval.approvalnodetask",
        "approval.leave",
        "dataset.dynamicformsubmission",
        "demo.book",
    ):
        assert (table, "dept_belong", "value.user.dept.ids") in write_scope
        # 新建数据可能显式带 creator（pre_save 不再自动补 dept_belong），
        # 必须同时按 creator.dept 兜底，否则演示管理员看不到自己刚建的单据。
        assert (table, "creator__dept", "value.user.dept.ids") in write_scope

    read_scope = {rule["table"] for rule in by_name["演示只读数据范围"]["rules"]}
    assert {
        "approval.approvalflow",
        "dataset.dynamicform",
        "dataset.dataset",
        "ai.aiknowledgedocument",
        "system.datadict",
        "system.tag",
        "system.userrole",
        "identity.deptinfo",
        "identity.post",
    } <= read_scope

    read_ext = {rule["table"] for rule in by_name["演示只读扩展"]["rules"]}
    assert {
        "system.menu",
        "system.datapermission",
        "system.modellabelfield",
        "audit.operationlog",
        "audit.userloginlog",
        "file.uploadfile",
        "task.exportrecord",
        "task.importrecord",
        "notifications.messagecontent",
        "task.webhooksubscription",
        "task.webhookdelivery",
        "identity.apiapplication",
        "task.taskexecution",
        "identity.accountrisk",
        "approval.approvaldelegation",
        "django_celery_beat.periodictask",
    } <= read_ext


def test_demo_role_has_avatar_upload_but_no_identity_writes():
    """保留部门内头像上传，但不得有用户/角色/部门写操作。"""

    assert "upload:SystemUser" in DEMO_ALLOWED_PERMISSION_NAMES
    assert "upload:UserInfo" in DEMO_ALLOWED_PERMISSION_NAMES
    for forbidden in (
        "create:SystemUser",
        "partialUpdate:SystemUser",
        "resetPassword:SystemUser",
        "unblock:SystemUser",
        "logout:SystemUser",
        "destroy:SystemUser",
        "create:SystemRole",
        "partialUpdate:SystemRole",
        "create:SystemDept",
        "partialUpdate:SystemDept",
        "regenerateSecret:IntegrationApiApp",
        "updateGrants:IntegrationApiApp",
    ):
        assert forbidden not in DEMO_ALLOWED_PERMISSION_NAMES


@pytest.mark.parametrize("path", [str(path) for path in _grant_files()])
def test_grant_files_are_valid_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data.get("version") == 1
    assert isinstance(data.get("roles"), dict)
