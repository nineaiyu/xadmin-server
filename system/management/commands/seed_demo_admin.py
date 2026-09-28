#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""演示账号 admin/admin123：对外开放的体验账号（README 承诺的线上演示口径）。

为什么要单独一个命令：

- README 把线上演示账号写成 ``admin/admin123``，但仓库里从无任何代码创建它，
  于是「演示账号」只存在于文档里，线上要么登录不了、要么只能把超管 xadmin
  暴露给公网——后者让访客拿到全量破坏能力，风险不可接受；
- 所以必须有一个**可登录、权限受控、且不进入正式初始化链路**的演示账号：
  它由本命令（seed_demo_* 卸载家族的一员）创建，绝不进 ``load_init_json``。

权限口径（用数据面而非代码里写 if 判断账号名——后者不可维护也不可审计）：

- 演示账号挂「演示模式」角色（loadjson/userrole.json 的 ``code=demo``，即线上
  已备好的演示角色）；
- 在此基础上把**会破坏演示环境的写操作**从该角色的菜单集合里摘掉（删数据 /
  批量删 / 回收站清除 / 导入覆盖 / 密码重置 / 角色与权限定义写入 / 部门授权 /
  任务调度写操作 / 机器凭证端点等）；
- 角色恢复/裁剪都落在角色菜单集合这一数据面上，命令幂等，``--clean-only``
  把角色菜单回滚为 loadjson 种子值并删掉演示账号（严格限定 ``admin`` 用户名且
  非超管——绝不触碰 xadmin/isummer 等真实超管）。

用法：

    python manage.py seed_demo_admin                # 幂等创建/更新演示账号
    python manage.py seed_demo_admin --reset        # 先清理再创建
    python manage.py seed_demo_admin --clean-only   # 只清理（seed_demo_clean 编排调用）
"""

import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from system.models import DeptInfo, Menu, UserInfo, UserRole

#: 演示账号：用户名/密码/昵称。用户名固定 admin（README 承诺对外口径），
#: 由数据库自增 pk 承载（UserInfo 主键为自增整型，非 UUID）。
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
ADMIN_NICKNAME = "演示管理员"

#: 演示角色（loadjson 种子定义，按 code 引用）
DEMO_ROLE_CODE = "demo"
DEMO_ROLE_NAME = "演示模式"

#: 演示账号所属部门候选（按序取首个存在的）：优先内置「演示部门」，回落到
#: 开箱模板的示例部门。只做人员归属展示，不参与权限判定。
ADMIN_DEPT_CODES = ("demo", "demo_rd")

#: 演示账号额外补充的页面：loadjson 的「演示模式」角色偏系统管理面，缺业务/
#: 分析/AI 页；这里补上「有演示数据可看」的页面，让 admin 能最大体验系统。
#: 这些页面下引出的破坏性写操作同样会被下面统一裁剪。
EXTRA_PAGE_PATHS = (
    "/approval/index",
    "/approval/instance/index",
    "/approval/leave/index",
    "/form-collection/my/index",
    "/integration/ai/index",
    "/integration/knowledge/index",
    "/analysis/dataset/index",
    "/analysis/dashboard/index",
    "/analysis/screen/index",
)

#: 精确摘除的权限码：单独列出是因为它们**不是**删除类也非受管模型写操作，
#: 却会破坏演示环境或成为提权通道：
#: - 角色/数据权限的建改 + 部门授权 = 改权限（能把自己或他人提为 SystemAdmin）；
#: - 用户建改 = 可经 roles 字段把 SystemAdmin 派给自己（提权通道，用户视角仍保留查看/导出）；
#: - Flower 建 = 任务调度写操作；
#: - MCP 端点 = 面向机器凭证的接入通道（与 seed_demo_org 排除口径一致）；
#: - 审批评论删除、知识库批量开关 = 数据删除类。
DENY_PERMISSIONS = {
    "create:SystemRole",
    "partialUpdate:SystemRole",
    "create:SystemDataPermission",
    "partialUpdate:SystemDataPermission",
    "empower:SystemDept",
    "create:SystemUser",
    "partialUpdate:SystemUser",
    "create:SystemFlower",
    "mcp:AiMcp",
    "deleteComment:SystemApprovalInstance",
    "batchToggle:AiKnowledge",
}

#: 按前缀摘除：批量删 / 导入覆盖 / 密码重置 / 知识库重扫与向量重建（外部成本）。
DENY_PREFIXES = ("batchDestroy:", "importData:", "resetPassword:", "syncRepo:", "buildEmbeddings:")

#: 定义类模型：其 create/update 属于「改配置」（数据集/看板/大屏/报表/知识库/
#: AI 供应商与配置），演示账号只保留查看与查询。
CONFIG_MODELS = {
    "DataDataset",
    "DataDashboard",
    "DataScreen",
    "DataReport",
    "AiKnowledge",
    "AiProfile",
    "AiAssistantConfig",
    "AiMcp",
}
WRITE_ACTIONS = {"create", "partialUpdate", "update"}


def is_destructive(menu: Menu) -> bool:
    """权限点是否会破坏演示环境（删数据 / 改配置 / 改权限 / 动任务与凭证）。"""
    if menu.menu_type != Menu.MenuChoices.PERMISSION:
        return False
    name = menu.name or ""
    if menu.method == Menu.MethodChoices.DELETE:
        return True
    if name.startswith(DENY_PREFIXES) or name in DENY_PERMISSIONS:
        return True
    action, _, target = name.partition(":")
    return target in CONFIG_MODELS and action in WRITE_ACTIONS


def _load_demo_role_menu_seed() -> list:
    """读取 loadjson 种子中「演示模式」角色的菜单主键（clean 回滚依据）。"""
    path = os.path.join(settings.PROJECT_DIR, "loadjson", "userrole.json")
    try:
        with open(path, encoding="utf-8") as fp:
            rows = json.load(fp)
    except (OSError, ValueError):
        return []
    for row in rows:
        if row["fields"].get("code") == DEMO_ROLE_CODE:
            return list(row["fields"].get("menu") or [])
    return []


class Command(BaseCommand):
    help = "创建/更新对外演示账号 admin（密码 admin123）并授予经裁剪的「演示模式」角色"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先清理演示账号并回滚角色菜单再重建")
        parser.add_argument("--clean-only", action="store_true", help="只清理（seed_demo_clean 编排调用）")

    def handle(self, *args, **options):
        if options.get("clean_only"):
            self._clean()
            return
        if options.get("reset"):
            self._clean()

        role = self._ensure_role_menus()
        self._ensure_admin(role)
        self.stdout.write(self.style.SUCCESS("seed_demo_admin done"))

    # ---------------------------------------------------------------- 角色菜单（数据面裁剪）

    def _demo_role(self) -> UserRole | None:
        return UserRole.objects.filter(code=DEMO_ROLE_CODE).first()

    def _extra_menu_ids(self) -> set:
        ids = set()
        for path in EXTRA_PAGE_PATHS:
            page = Menu.objects.filter(path=path, menu_type=Menu.MenuChoices.MENU).first()
            if page is None:
                continue
            ids.add(page.pk)
            if page.parent_id:
                ids.add(page.parent_id)
            ids.update(Menu.objects.filter(parent=page).values_list("pk", flat=True))
        return ids

    def _ensure_role_menus(self) -> UserRole | None:
        role = self._demo_role()
        if role is None:
            self.stdout.write(
                self.style.WARNING(f"role '{DEMO_ROLE_CODE}' not found (run load_init_json first); role skipped")
            )
            return None
        candidates = set(role.menu.values_list("pk", flat=True)) | self._extra_menu_ids()
        menus = list(Menu.objects.filter(pk__in=candidates))
        keep = [menu.pk for menu in menus if not is_destructive(menu)]
        removed = [menu.name for menu in menus if is_destructive(menu)]
        role.menu.set(keep)
        self.stdout.write(f"demo role menus: keep {len(keep)}, pruned {len(removed)}")
        if removed:
            self.stdout.write(f"pruned destructive permissions: {', '.join(sorted(removed))}")
        return role

    def _restore_role_menus(self, role: UserRole) -> None:
        """把角色菜单回滚为 loadjson 种子值（仅回滚种子中确实存在的菜单）。"""
        seed_ids = _load_demo_role_menu_seed()
        existing = set(Menu.objects.filter(pk__in=seed_ids).values_list("pk", flat=True))
        if not existing:
            # 种子菜单不在库中（测试库/未跑 load_init_json）：不把角色菜单清空
            return
        role.menu.set(existing)

    # ---------------------------------------------------------------- 演示账号

    def _ensure_admin(self, role: UserRole | None) -> None:
        existing = UserInfo.all_objects.filter(username=ADMIN_USERNAME).first()
        if existing is not None and existing.is_superuser:
            # 绝不改写真实超管的密码/权限：同名 superuser 视为正式账号，跳过
            self.stdout.write(
                self.style.WARNING(f"'{ADMIN_USERNAME}' is a superuser; demo account skipped to avoid hijacking")
            )
            return

        dept = None
        for code in ADMIN_DEPT_CODES:
            dept = DeptInfo.objects.filter(code=code).first()
            if dept is not None:
                break

        if existing is None:
            user = UserInfo(
                username=ADMIN_USERNAME,
                nickname=ADMIN_NICKNAME,
                email=f"{ADMIN_USERNAME}@demo.local",
                is_active=True,
                is_superuser=False,
                is_staff=False,
                dept=dept,
                # 用户级方式白名单收窄为空集：共享演示账号不允许绑定任何 MFA——
                # 否则访客绑上 OTP 后，策略强制二次验证会锁死公共登录（绑定入口
                # 校验 get_user_mfa_policy，验证侧 get_enabled_backends 同口径放空）。
                allowed_mfa_types=["none"],
            )
            user.set_password(ADMIN_PASSWORD)
            user.save()
            self.stdout.write(f"created demo account: {ADMIN_USERNAME}")
        else:
            user = existing
            user.nickname = user.nickname or ADMIN_NICKNAME
            user.deleted_at = None  # 回收站中的演示账号复活复用
            user.is_active = True
            user.is_superuser = False
            user.is_staff = False
            user.allowed_mfa_types = ["none"]
            if dept is not None:
                user.dept = dept
            user.set_password(ADMIN_PASSWORD)
            user.save()
            self.stdout.write(f"updated demo account: {ADMIN_USERNAME}")

        if role is not None:
            user.roles.set([role])
            self.stdout.write(f"granted role '{DEMO_ROLE_CODE}' to {ADMIN_USERNAME}")

    # ---------------------------------------------------------------- 清理

    def _clean(self):
        # 严格限定用户名 admin 且非超管：绝不误伤 xadmin/isummer 等真实超管
        removed = UserInfo.all_objects.filter(username=ADMIN_USERNAME, is_superuser=False).delete()[0]
        self.stdout.write(f"removed demo account: {removed}")

        role = self._demo_role()
        if role is not None:
            self._restore_role_menus(role)
            self.stdout.write("restored demo role menus from seed")
