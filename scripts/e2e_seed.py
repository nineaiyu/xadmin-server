#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""E2E 环境种子脚本：一键重置并填充 Playwright 所需数据。

用法（配合 tests/settings_e2e.py，PG 真库 + 进程内 FakeRedis；依赖 compose.test.yml）：

    cd xadmin-server
    DJANGO_SETTINGS_MODULE=tests.settings_e2e XADMIN_ADMIN_PASSWORD='E2E-Admin-2026!' \
        .venv/bin/python scripts/e2e_seed.py

步骤：DROP/CREATE 独立库（WITH (FORCE) 兜底残留连接）→ migrate → ops/init_data
初始化（菜单/角色/超管）→ 创建 E2E 场景用户（普通用户 / 受限用户 / 数据权限 /
字段权限 / 锁定测试）。
"""

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

# 场景种子拆至同目录模块（scripts/ 非包目录：按脚本执行时其目录天然在 sys.path[0]）。
# 必须在 main() 执行前导入——文件尾导入对"原模块自身 main() 的调用"不生效（NameError）。
from e2e_seed_scenes import (  # noqa: E402
    disable_login_mfa_policy,
    seed_demo_book_scene,
    seed_directory_scene,
    seed_monitor_scene,
    seed_oauth_im_provider,
    seed_periodic_task,
    seed_user_notice_scene,
)

# (username, password, nickname, is_superuser, role_code)
# role_code 为 None 表示不绑定任何角色（无菜单权限）
E2E_USERS = [
    ("e2e_user", "E2E-User-2026!", "E2E普通用户", False, None),
    ("e2e_scoped", "E2E-Scoped-2026!", "E2E受限用户", False, None),
    ("e2e_dp", "E2E-DataPerm-2026!", "E2E数据权限用户", False, "e2e_dp"),
    ("e2e_fp", "E2E-FieldPer-2026!", "E2E字段权限用户", False, "e2e_fp"),
    ("e2e_lock", "E2E-Lock-2026!", "E2E锁定测试用户", False, None),
    # 部门主管场景：e2e_leader 主管测试部门（本人兼成员），用户列表可见本人 + 部门成员
    ("e2e_leader", "E2E-Leader-2026!", "E2E主管用户", False, "e2e_leader"),
    ("e2e_member", "E2E-Member-2026!", "E2E部门成员", False, None),
    # 审批人：第二超管（申请人 xadmin 不能自审，审批中心用例以其身份通过审批单）
    ("e2e_approver", "E2E-Approver-2026!", "E2E审批人", True, None),
]

# 数据权限规则：用户列表仅可见「id 等于本人」的记录（运行时 value 被替换为当前用户 pk）
DATA_PERMISSION_RULES = [
    {
        "table": "identity.userinfo",
        "field": "id",
        "type": "value.user.id",
        "match": "exact",
        "value": "",
        "exclude": False,
    }
]

# 部门主管规则：用户列表可见「主管部门成员」（运行时解析为 leader 部门递归成员 pk）
DATA_PERMISSION_LEADER_RULES = [
    {
        "table": "identity.userinfo",
        "field": "id",
        "type": "value.leader.user.ids",
        "match": "in",
        "value": "*",
        "exclude": False,
    }
]

# 数据权限规则：全部数据（value.all），用于字段权限场景放行行可见性
# （数据权限默认拒绝：无任何授权的用户列表返回 none，见 packages/xadmin-common/common/core/filter.py）
DATA_PERMISSION_ALL_RULES = [
    {"table": "identity.userinfo", "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}
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


def get_user_list_api_menu():
    """「用户列表」GET 接口权限菜单（api/system/user$ + method=GET）。

    同一 path 每个HTTP方法各有一条菜单，字段权限必须挂在 GET 菜单上，
    否则列表请求（IsAuthenticated 按当前请求菜单 pk 查 FieldPermission）
    查不到白名单，行内容被裁剪成空对象。
    """
    from system.models import Menu

    return Menu.objects.filter(
        path="api/system/user$",
        menu_type=Menu.MenuChoices.PERMISSION,
        method="GET",
    ).first()


def get_user_detail_api_menu():
    """「用户详情」GET 接口权限菜单（api/system/user/{pk}$）。

    编辑态取原文（?mask=false）走的就是详情 GET，字段白名单必须同样覆盖该菜单，
    否则响应被裁剪成空对象，编辑表单只能拿到列表里的掩码值。
    """
    from system.models import Menu

    return Menu.objects.filter(
        path="api/system/user/(?P<pk>[^/.]+)$",
        menu_type=Menu.MenuChoices.PERMISSION,
        method="GET",
    ).first()


def grant_field_permission(role, excluded_field):
    """为用户列表 / 详情的字段白名单授权（除 excluded_field 外全部 userinfo 字段）。

    字段权限为白名单制：角色+菜单没有任何 FieldPermission 时序列化字段全被
    裁剪（tests/unit/common/test_serializer_field_permission.py 锁定的语义），
    因此数据权限场景角色也必须拿到字段白名单，否则行内容为空对象。
    """
    from system.models import FieldPermission, ModelLabelField

    list_menu = get_user_list_api_menu()
    root = ModelLabelField.objects.filter(
        name="identity.userinfo", parent__isnull=True, field_type=ModelLabelField.FieldChoices.ROLE
    ).first()
    if not (role and list_menu and root):
        print(f"skip field permission: role={bool(role)} menu={bool(list_menu)} root={bool(root)}")
        return None
    fields = ModelLabelField.objects.filter(parent=root).exclude(name=excluded_field)
    fp = None
    for menu in [list_menu, get_user_detail_api_menu()]:
        if not menu:
            continue
        fp, _ = FieldPermission.objects.get_or_create(role=role, menu=menu)
        fp.field.set(fields)
    return fp


def reset_pg_database() -> None:
    """真库化重置（阶段 4）：DROP DATABASE WITH (FORCE) + CREATE，取代 sqlite「删文件重开」。

    连接参数与库名以 tests/settings_e2e 为单一来源（_e2e_config / _e2e_db_name，
    并行跑批按 shard 独立库名）；FORCE 兜底上一轮未退净的 daphne 残留连接（PG 13+，
    容器为 17）；管理连接走 postgres 库。PG 不可达时给与 conftest 预检一致的恢复指引。
    """
    import psycopg

    from tests.settings_e2e import _e2e_config, _e2e_db_name

    try:
        # autocommit：DROP/CREATE DATABASE 不得运行在事务块内（psycopg 默认隐式开启）
        admin = psycopg.connect(
            dbname="postgres",
            host=_e2e_config["DB_HOST"],
            port=_e2e_config["DB_PORT"],
            user=_e2e_config["DB_USER"],
            password=_e2e_config["DB_PASSWORD"],
            connect_timeout=3,
            autocommit=True,
        )
    except psycopg.OperationalError as e:
        raise SystemExit(
            f"E2E PostgreSQL 不可达（{_e2e_config['DB_HOST']}:{_e2e_config['DB_PORT']}）：{e}\n"
            "先起依赖：docker compose -f compose.test.yml up -d"
        ) from e
    with admin:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{_e2e_db_name}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{_e2e_db_name}"')
    admin.close()
    print(f"reset pg database: {_e2e_db_name}")


def main() -> None:
    reset_pg_database()

    import django

    django.setup()

    from django.core import management

    # run_syncdb=True 对齐 Django 测试库行为（未携带迁移文件的 app 按当前模型直接建表；
    # demo app 已恢复标准迁移并随 migrate 正常建表——历史上"级联删除触及 demo_book
    # 报 no such table"的根因是无迁移且未 run-syncdb，已由提交迁移收口）
    management.call_command("migrate", run_syncdb=True, verbosity=0, interactive=False)
    print("migrate done")

    # 初始化基础数据（菜单/角色/超管），密码取 XADMIN_ADMIN_PASSWORD
    from ops.init_data import main as init_data_main

    sys.argv = ["init_data"]
    init_data_main()

    from identity.models import UserInfo, UserRole
    from system.models import DataPermission

    created_users = {}
    for username, password, nickname, is_superuser, role_code in E2E_USERS:
        if UserInfo.objects.filter(username=username).exists():
            continue
        if is_superuser:
            user = UserInfo.objects.create_superuser(
                username=username, email=f"{username}@example.com", password=password, nickname=nickname
            )
        else:
            user = UserInfo.objects.create_user(username=username, password=password, nickname=nickname)
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
            defaults={"rules": DATA_PERMISSION_RULES, "mode_type": DataPermission.ModeChoices.OR, "is_active": True},
        )
        dp.menu.clear()
        e2e_dp.rules.add(dp)
        dp_role = e2e_dp.roles.filter(code="e2e_dp").first()
        if dp_role:
            grant_user_management_menus(dp_role)
            # 字段权限白名单（全字段）：否则行内容被裁剪成空对象
            grant_field_permission(dp_role, excluded_field=None)
        print("data permission seeded for e2e_dp")

    # ---- 部门主管场景：e2e_leader 主管测试部门（本人兼成员），列表可见本人 + 部门成员 ----
    from identity.models import DeptInfo

    e2e_leader = created_users.get("e2e_leader") or UserInfo.objects.filter(username="e2e_leader").first()
    e2e_member = created_users.get("e2e_member") or UserInfo.objects.filter(username="e2e_member").first()
    if e2e_leader and e2e_member:
        dept, _ = DeptInfo.objects.get_or_create(code="e2e_leader_dept", defaults={"name": "E2E-主管测试部"})
        if dept.leader_id != e2e_leader.pk:
            dept.leader = e2e_leader
            dept.save(update_fields=["leader"])
        for member in (e2e_leader, e2e_member):
            if member.dept_id != dept.pk:
                member.dept = dept
                member.save(update_fields=["dept"])
        leader_dp, _ = DataPermission.objects.get_or_create(
            name="E2E-主管部门成员",
            defaults={
                "rules": DATA_PERMISSION_LEADER_RULES,
                "mode_type": DataPermission.ModeChoices.OR,
                "is_active": True,
            },
        )
        leader_dp.menu.clear()
        e2e_leader.rules.add(leader_dp)
        leader_role = e2e_leader.roles.filter(code="e2e_leader").first()
        if leader_role:
            grant_user_management_menus(leader_role)
            # 字段权限白名单（全字段）：否则行内容被裁剪成空对象
            grant_field_permission(leader_role, excluded_field=None)
        print("leader data permission seeded for e2e_leader")

    # ---- 字段权限场景：e2e_fp 的用户列表隐藏「手机」列 ----
    # 选 phone 而非 email：UserInfo 序列化器 table_fields 不含 email（列默认不渲染，
    # 表头断言无从谈起）；phone 在默认表格列中（table_show 有序号）
    e2e_fp = created_users.get("e2e_fp") or UserInfo.objects.filter(username="e2e_fp").first()
    if e2e_fp:
        fp_role = e2e_fp.roles.filter(code="e2e_fp").first()
        if fp_role:
            # 行可见性：数据权限默认拒绝，需授予「全部数据」规则
            dp_all, _ = DataPermission.objects.get_or_create(
                name="E2E-全部用户数据",
                defaults={
                    "rules": DATA_PERMISSION_ALL_RULES,
                    "mode_type": DataPermission.ModeChoices.OR,
                    "is_active": True,
                },
            )
            dp_all.menu.clear()
            e2e_fp.rules.add(dp_all)
            grant_user_management_menus(fp_role)
            # 字段白名单（除 phone 外全部字段）
            grant_field_permission(fp_role, excluded_field="phone")
            print("field permission seeded for e2e_fp (phone hidden)")

    # ---- 定时任务管理页演示数据 ----
    seed_periodic_task()

    # ---- 敏感操作告警关闭：删除类用例触发 WS 站内信实时弹窗，盖在抽屉按钮上 ----
    # 造成点击「element is not stable」（回收站恢复用例实测命中）；告警链路由单测覆盖。
    # 注意：方法清单为空 =「不按方法过滤」（全告警），须用永不命中的哨兵值关闭
    from common.core.config import SysConfig

    SysConfig.set_value("SENSITIVE_OPERATION_METHODS", ["__E2E_DISABLED__"])
    print("sensitive operation alert disabled")

    # ---- 手动任务白名单扩容：用例要手工调度种子任务 system.tasks.auto_clean_operation_job ----
    # 种子默认仅放行演示任务（demo.tasks.auto_off_shelf_books），任务管理页用例
    # 创建周期任务/立即执行会被写入侧白名单拦截（400），测试库在此扩容。
    SysConfig.set_value(
        "MANUAL_RUNNABLE_TASKS",
        ["demo.tasks.auto_off_shelf_books", "system.tasks.auto_clean_operation_job"],
    )
    print("manual runnable tasks extended")

    # ---- 登录页第三方入口（feishu flavor）----
    seed_oauth_im_provider()

    # ---- 我的通知场景：e2e_user 授权通知页 + 未读通知 ----
    seed_user_notice_scene()

    # ---- 系统监控场景：心跳历史与告警记录（监控页图表/告警卡数据源）----
    seed_monitor_scene()

    # ---- 二开样板页场景：生成器产出的 demo.Book 菜单与权限点（防样板腐烂）----
    seed_demo_book_scene()

    # ---- 通讯录场景：岗位清单与成员（按岗位视角的真实数据）----
    seed_directory_scene()

    # ---- 登录辅助安全项：非工作时间 MFA 策略会让夜间跑批的登录被拦截 ----
    disable_login_mfa_policy()

    print("E2E seed done")


if __name__ == "__main__":
    main()
