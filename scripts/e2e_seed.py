#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E2E 环境种子脚本：一键重置并填充 Playwright 所需数据。

用法（配合 tests/settings_e2e.py，sqlite 文件库 + 进程内 FakeRedis）：

    cd xadmin-server
    DJANGO_SETTINGS_MODULE=tests.settings_e2e XADMIN_ADMIN_PASSWORD='E2E-Admin-2026!' \
        .venv/bin/python scripts/e2e_seed.py

步骤：删除旧库 → migrate → utils/init_data 初始化（菜单/角色/超管）→
创建 E2E 场景用户（普通用户 / 受限用户 / 数据权限 / 字段权限 / 锁定测试）。
"""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

E2E_DB = os.path.join(PROJECT_DIR, "tmp", "e2e.sqlite3")

# (username, password, nickname, is_superuser, role_code)
# role_code 为 None 表示不绑定任何角色（无菜单权限）
E2E_USERS = [
    ("e2e_user", "E2E-User-2026!", "E2E普通用户", False, None),
    ("e2e_scoped", "E2E-Scoped-2026!", "E2E受限用户", False, None),
    ("e2e_dp", "E2E-DataPerm-2026!", "E2E数据权限用户", False, "e2e_dp"),
    ("e2e_fp", "E2E-FieldPer-2026!", "E2E字段权限用户", False, "e2e_fp"),
    ("e2e_lock", "E2E-Lock-2026!", "E2E锁定测试用户", False, None),
]

# 数据权限规则：用户列表仅可见「id 等于本人」的记录（运行时 value 被替换为当前用户 pk）
DATA_PERMISSION_RULES = [
    {"table": "system.userinfo", "field": "id", "type": "value.user.id",
     "match": "exact", "value": "", "exclude": False}
]


def grant_user_management_menus(role):
    """授予「系统管理 → 用户管理」页面及其全部接口权限菜单。"""
    from system.models import Menu

    page_menu = Menu.objects.filter(path="/system/user/index", menu_type=Menu.MenuChoices.MENU).first()
    if not page_menu:
        print("skip role menus: /system/user/index menu not found")
        return
    menus = [page_menu]
    if page_menu.parent_id:
        menus.append(page_menu.parent)
    menus.extend(Menu.objects.filter(parent=page_menu, menu_type=Menu.MenuChoices.PERMISSION))
    role.menu.set(menus)


def main() -> None:
    if os.path.exists(E2E_DB):
        os.remove(E2E_DB)
        print(f"removed old db: {E2E_DB}")
    os.makedirs(os.path.dirname(E2E_DB), exist_ok=True)

    import django

    django.setup()

    from django.core import management

    management.call_command("migrate", verbosity=0, interactive=False)
    print("migrate done")

    # 初始化基础数据（菜单/角色/超管），密码取 XADMIN_ADMIN_PASSWORD
    from utils.init_data import main as init_data_main

    sys.argv = ["init_data"]
    init_data_main()

    from system.models import (
        DataPermission, FieldPermission, Menu, ModelLabelField, UserInfo, UserRole
    )

    created_users = {}
    for username, password, nickname, is_superuser, role_code in E2E_USERS:
        if UserInfo.objects.filter(username=username).exists():
            continue
        user = UserInfo.objects.create_user(
            username=username, password=password, nickname=nickname
        )
        if role_code:
            role, _ = UserRole.objects.get_or_create(name=f"E2E-{role_code}", code=role_code)
            user.roles.add(role)
        user.is_active = True
        user.save()
        created_users[username] = user
        print(f"created e2e user: {username}")

    # ---- 数据权限场景：e2e_dp 的用户列表仅返回本人（menu 不绑定 = 全局生效）----
    e2e_dp = created_users.get("e2e_dp") or UserInfo.objects.filter(username="e2e_dp").first()
    if e2e_dp:
        dp, _ = DataPermission.objects.get_or_create(
            name="E2E-仅本人用户数据",
            defaults={"rules": DATA_PERMISSION_RULES, "mode_type": DataPermission.ModeChoices.OR,
                      "is_active": True},
        )
        dp.menu.clear()
        e2e_dp.rules.add(dp)
        dp_role = e2e_dp.roles.filter(code="e2e_dp").first()
        if dp_role:
            grant_user_management_menus(dp_role)
        print("data permission seeded for e2e_dp")

    # ---- 字段权限场景：e2e_fp 的用户列表隐藏「邮件」列（白名单 = 除 email 外全部字段）----
    e2e_fp = created_users.get("e2e_fp") or UserInfo.objects.filter(username="e2e_fp").first()
    if e2e_fp:
        fp_role = e2e_fp.roles.filter(code="e2e_fp").first()
        list_menu = Menu.objects.filter(
            path="api/system/user$", menu_type=Menu.MenuChoices.PERMISSION
        ).first()
        root = ModelLabelField.objects.filter(
            name="system.userinfo", parent__isnull=True, field_type=ModelLabelField.FieldChoices.ROLE
        ).first()
        if fp_role and list_menu and root:
            fp, _ = FieldPermission.objects.get_or_create(role=fp_role, menu=list_menu)
            fp.field.set(ModelLabelField.objects.filter(parent=root).exclude(name="email"))
            grant_user_management_menus(fp_role)
            print("field permission seeded for e2e_fp (email hidden)")
        else:
            print(f"skip field permission: role={bool(fp_role)} menu={bool(list_menu)} root={bool(root)}")

    print("E2E seed done")


if __name__ == "__main__":
    main()
