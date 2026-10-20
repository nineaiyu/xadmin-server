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
- 角色菜单采用 **allow-list**（``demo_seed.permissions``）：只保留有演示数据、
  写操作只影响演示数据的页面与权限点；身份治理、系统配置、日志、监控、任务调度、
  Webhook / 开放平台等一律不在清单内；
- 演示账号的数据范围由部门子树收敛：演示部门是顶层部门，只挂「演示部门及以下」
  数据权限；用户管理 / 头像上传只能命中演示子树内的用户，真实用户与超管不可见；
- 角色恢复/裁剪都落在角色菜单集合这一数据面上，命令幂等，``--clean-only``
  把角色菜单回滚为 allow-list 并删掉演示账号（严格限定 ``admin`` 用户名且
  非超管——绝不触碰 xadmin/isummer 等真实超管）。

用法：

    python manage.py seed_demo_admin                # 幂等创建/更新演示账号
    python manage.py seed_demo_admin --reset        # 先清理再创建
    python manage.py seed_demo_admin --clean-only   # 只清理（seed_demo_clean 编排调用）
"""

import json
from typing import Any

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db.models import Q

from common.utils import get_logger
from demo_seed.permissions import (
    DEMO_ADMIN_USERNAME,
    DEMO_PAGE_PATHS,
    DEMO_ROLE_CODE,
    demo_menu_ids,
)
from identity.models import DeptInfo, UserInfo, UserRole
from system.models import FieldPermission, Menu
from system.services.permission_sync import fill_field_permissions
from system.services.seeding import platform_file

logger = get_logger(__name__)

#: 演示账号：用户名/密码/昵称。用户名固定 admin（README 承诺对外口径），
#: 由数据库自增 pk 承载（UserInfo 主键为自增整型，非 UUID）。
ADMIN_USERNAME = DEMO_ADMIN_USERNAME
ADMIN_PASSWORD = "admin123"
ADMIN_NICKNAME = "演示管理员"

#: 演示角色（loadjson 种子定义，按 code 引用）
DEMO_ROLE_NAME = "演示模式"
DEMO_DEPT_CODE = "demo"

#: 演示账号所属部门候选（按序取首个存在的）：必须是演示租户根部门或其子树，
#: 部门直接决定演示账号的数据范围（「演示部门及以下」），不能回落到真实组织。
ADMIN_DEPT_CODES = ("demo", "demo_rd")

#: 兼容旧引用：页面范围已收敛到 demo_seed.permissions 的 allow-list。
EXTRA_PAGE_PATHS = DEMO_PAGE_PATHS

#: allow-list 之外的二次安全兜底：即使白名单误加，也不得放行这些提权/破坏类权限。
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

#: 二次兜底：批量删 / 导入覆盖 / 密码重置 / 知识库重扫与向量重建（外部成本）。
DENY_PREFIXES = ("batchDestroy:", "importData:", "resetPassword:", "syncRepo:", "buildEmbeddings:")

#: 二次兜底：定义类模型的 create/update 属于「改配置」（数据集/看板/大屏/报表/知识库/
#: AI 供应商与配置/表单与流程定义/字典与脱敏规则/Webhook 与应用配置），
#: 演示账号只保留查看与查询——改配置会连带影响其他演示者的可见数据面。
CONFIG_MODELS = {
    "DataDataset",
    "DataDashboard",
    "DataScreen",
    "DataReport",
    "AiKnowledge",
    "AiProfile",
    "AiAssistantConfig",
    "AiMcp",
    "ScreenTemplate",
    "ScreenClipboard",
    "FormDesigner",
    "SystemApprovalFlow",
    "WebhookSubscription",
    "IntegrationApiApp",
    "SystemDataDict",
    "SystemDataMaskRule",
    "ApprovalDelegation",
}
WRITE_ACTIONS = {"create", "partialUpdate", "update"}


def is_destructive(menu: Menu) -> bool:
    """allow-list 命中的菜单再做一次破坏性兜底（删数据 / 改配置 / 改权限 / 动任务与凭证）。"""
    if menu.menu_type != Menu.MenuChoices.PERMISSION:
        return False
    name = menu.name or ""
    if menu.method == Menu.MethodChoices.DELETE:
        return True
    if name.startswith(DENY_PREFIXES) or name in DENY_PERMISSIONS:
        return True
    action, _, target = name.partition(":")
    return target in CONFIG_MODELS and action in WRITE_ACTIONS


def _demo_dept() -> DeptInfo | None:
    return DeptInfo.objects.filter(code=DEMO_DEPT_CODE).first()


def ensure_demo_dept_tree() -> None:
    """把演示部门收敛为独立顶层租户（幂等）。

    数据权限沿部门祖先链继承且「取最宽生效」：演示部门一旦挂在
    ``xAdmin管理团队`` 下，就会继承其「全部数据」授权，演示管理员随即能
    看到/操作真实用户。这里强制演示部门为顶层，并把示例研发/财务部挂到
    演示部门下，保证「演示部门及以下」数据权限覆盖全部演示用户。
    ``test``（注册默认部门）与 ``default`` 不是管理租户，一并移出父链，
    避免继承 xAdmin 管理团队的「全部数据」。
    """

    demo_dept = _demo_dept()
    if demo_dept is None:
        return
    if demo_dept.parent_id:
        demo_dept.parent = None
        demo_dept.save(update_fields=["parent", "updated_time"])
    for code in ("demo_rd", "demo_fin"):
        dept = DeptInfo.objects.filter(code=code).first()
        if dept is not None and dept.parent_id != demo_dept.pk:
            dept.parent = demo_dept
            dept.save(update_fields=["parent", "updated_time"])
    test_dept = DeptInfo.objects.filter(code="test").first()
    if test_dept is not None and test_dept.parent_id:
        test_dept.parent = None
        test_dept.save(update_fields=["parent", "updated_time"])
    # 默认部门 / 测试部门是种子内置的非管理租户：挂在 xAdmin 管理团队下会继承
    # 「全部数据」，存量库由自愈任务一并移出（新装库由 loadjson/deptinfo.json 保证）。
    default_dept = DeptInfo.objects.filter(code="default").first()
    if default_dept is not None and default_dept.parent_id:
        default_dept.parent = None
        default_dept.save(update_fields=["parent", "updated_time"])


def _backfill_dept_belong(model_label: str, *, creator_scoped: bool) -> int:
    """把演示数据的 dept_belong 回填到演示部门子树（幂等）。

    ``creator_scoped=True`` 时只处理演示账号创建、且 dept_belong 为空的行，
    并按创建人部门回填（创建人无部门时回落到演示部门）；``False`` 时处理
    演示 app 内全部 dept_belong 为空的行（如示例图书，创建人是超管）。
    """

    model = django_apps.get_model(model_label)
    if model is None or not any(field.name == "dept_belong" for field in model._meta.fields):
        return 0
    demo_dept = _demo_dept()
    if demo_dept is None:
        return 0

    queryset = model.objects.filter(dept_belong__isnull=True)
    if not creator_scoped:
        return queryset.update(dept_belong=demo_dept)

    queryset = queryset.filter(
        Q(creator__username__startswith="demo_") | Q(creator__username=ADMIN_USERNAME, creator__is_superuser=False)
    ).select_related("creator")
    changed = 0
    for row in queryset:
        row.dept_belong = getattr(row.creator, "dept", None) or demo_dept
        row.save(update_fields=["dept_belong", "updated_time"])
        changed += 1
    return changed


def backfill_demo_scope() -> None:
    """把演示用户与演示业务数据收敛到演示部门子树（幂等）。

    新建环境由 seed_demo_* 直接写入；存量库（历史种子 dept_belong 为空）
    由本函数补齐。演示账号自愈任务每天调用 ``seed_demo_admin``，因此存量
    数据也会随之收敛，无需手工迁移。
    """

    demo_dept = _demo_dept()
    if demo_dept is None:
        return

    subtree = set(DeptInfo.dept_tree_pks([demo_dept.pk]))
    demo_users = UserInfo.objects.filter(
        Q(username__startswith="demo_") | Q(username=ADMIN_USERNAME, is_superuser=False)
    ).exclude(dept_id__in=subtree)
    moved_users = 0
    for user in demo_users:
        user.dept = demo_dept
        user.save(update_fields=["dept", "updated_time"])
        moved_users += 1

    changed_rows = 0
    for model_label in (
        "approval.approvalinstance",
        "approval.approvalrequest",
        "approval.leave",
        "dataset.dynamicformsubmission",
    ):
        changed_rows += _backfill_dept_belong(model_label, creator_scoped=True)
    changed_rows += _backfill_dept_belong("demo.book", creator_scoped=False)

    # 节点任务与实例同域：实例回填后再把任务的 dept_belong 对齐，避免任务列表
    # 因 dept_belong 为空被数据权限裁掉（演示审批页的待办/进度会缺行）。
    task_model = django_apps.get_model("approval", "ApprovalNodeTask")
    if task_model is not None and any(field.name == "dept_belong" for field in task_model._meta.fields):
        tasks = task_model.objects.filter(dept_belong__isnull=True, instance__dept_belong__isnull=False).select_related(
            "instance"
        )
        for task in tasks:
            task.dept_belong = task.instance.dept_belong
            task.save(update_fields=["dept_belong", "updated_time"])
            changed_rows += 1

    if moved_users or changed_rows:
        logger.info("demo scope backfilled: users=%s rows=%s", moved_users, changed_rows)


class Command(BaseCommand):
    help = "创建/更新对外演示账号 admin（密码 admin123）并授予 allow-list 演示角色"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--reset", action="store_true", help="先清理演示账号并回滚角色菜单再重建")
        parser.add_argument("--clean-only", action="store_true", help="只清理（seed_demo_clean 编排调用）")

    def handle(self, *args: Any, **options: Any) -> None:
        if options.get("clean_only"):
            self._clean()
            return
        if options.get("reset"):
            self._clean()

        ensure_demo_dept_tree()
        role = self._ensure_role_menus()
        self._ensure_admin(role)
        self._ensure_field_permissions(role)
        backfill_demo_scope()
        self.stdout.write(self.style.SUCCESS("seed_demo_admin done"))

    # ---------------------------------------------------------------- 角色菜单（allow-list）

    def _demo_role(self) -> UserRole | None:
        role: UserRole | None = UserRole.objects.filter(code=DEMO_ROLE_CODE).first()
        return role

    def _allowed_menu_ids(self) -> set[Any]:
        """按 allow-list 计算目标菜单，并保留 ``is_destructive`` 兜底。"""

        target = demo_menu_ids()
        menus = list(Menu.objects.filter(pk__in=target))
        keep = [menu.pk for menu in menus if not is_destructive(menu)]
        rejected = sorted(menu.name for menu in menus if is_destructive(menu))
        if rejected:
            self.stdout.write(
                self.style.WARNING(f"allow-list contains destructive permissions, rejected: {', '.join(rejected)}")
            )
        return set(keep)

    def _ensure_role_menus(self) -> UserRole | None:
        role = self._demo_role()
        if role is None:
            self.stdout.write(
                self.style.WARNING(f"role '{DEMO_ROLE_CODE}' not found (run load_init_json first); role skipped")
            )
            return None
        keep = self._allowed_menu_ids()
        role.menu.set(keep)
        self.stdout.write(f"demo role menus: {len(keep)} allow-listed")
        return role

    def _restore_role_menus(self, role: UserRole) -> None:
        """把角色菜单回滚为 allow-list（clean 回滚依据）。"""

        keep = self._allowed_menu_ids()
        if not keep:
            # 种子菜单不在库中（测试库/未跑 load_init_json）：不把角色菜单清空
            return
        role.menu.set(keep)

    # ---------------------------------------------------------------- 字段权限（白名单补齐）

    def _ensure_field_permissions(self, role: UserRole | None) -> None:
        """补齐角色「已授权权限点 × 绑定模型全字段」的字段白名单缺口（幂等）。

        字段权限为 fail-closed 的零字段口径：角色在某权限点上没有非空白名单时，
        接口输出空对象，前端列表/详情整块空白。演示角色菜单横跨系统管理面与业务/
        分析/AI 页，白名单依赖种子静态列举，角色菜单新增页面后必然漏配——这里按
        缺口自动补齐，保证演示账号打开任何已授权页面都有字段展示。
        """
        if role is None:
            return
        filled = fill_field_permissions(roles=[role])
        self.stdout.write(f"demo role field permissions: filled {len(filled)} missing (role, menu) pairs")

    def _field_permission_seed_menus(self, role: UserRole) -> set[Any]:
        """读取 loadjson 种子中该角色的字段权限菜单主键（clean 回滚依据）。"""
        path = platform_file("fieldpermission.json")
        try:
            with open(path, encoding="utf-8") as fp:
                rows = json.load(fp)
        except (OSError, ValueError):
            return set()
        role_pk = str(role.pk)
        return {row["fields"]["menu"] for row in rows if str(row["fields"].get("role")) == role_pk}

    def _prune_role_field_permissions(self, role: UserRole) -> None:
        """回滚字段权限到种子口径：删除种子未声明的补齐项（保留种子既有配置）。"""
        seed_menus = self._field_permission_seed_menus(role)
        removed = FieldPermission.objects.filter(role=role).exclude(menu_id__in=seed_menus).delete()[0]
        if removed:
            self.stdout.write(f"pruned demo role field permissions: {removed}")

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

    def _clean(self) -> None:
        # 严格限定用户名 admin 且非超管：绝不误伤 xadmin/isummer 等真实超管
        removed = UserInfo.all_objects.filter(username=ADMIN_USERNAME, is_superuser=False).delete()[0]
        self.stdout.write(f"removed demo account: {removed}")

        role = self._demo_role()
        if role is not None:
            self._restore_role_menus(role)
            self._prune_role_field_permissions(role)
            self.stdout.write("restored demo role menus from allow-list")
