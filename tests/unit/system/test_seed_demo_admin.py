# -*- coding: utf-8 -*-
"""演示账号 seed_demo_admin / 补充数据 seed_demo_extras 守护。

覆盖：
1. 演示账号 admin/admin123 可登录、非超管、挂「演示模式」角色且归属演示部门；
2. 角色菜单裁剪：破坏性写操作（删/批量删/导入/密码重置/改权限/任务调度写）被摘掉，
   查看类权限点保留；重复执行幂等；
3. 同名超管（username=admin 的真实账号）不被改写/提权；
4. seed_demo_clean 清理演示账号但不误伤超管；
5. extras：岗位/标签/AI 会话/导出记录幂等生成、清理归零且不动内置标签。
"""

import pytest
from django.core.management import call_command

from ai.models.ai import AiChatMessage
from system.management.commands.seed_demo_admin import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    DEMO_ROLE_CODE,
    DENY_PERMISSIONS,
    is_destructive,
)
from system.management.commands.seed_demo_extras import (
    AI_MESSAGE_PLAN,
    EXPORT_PKS,
    POST_ASSIGN,
    POST_CODE_PREFIX,
    POST_PLAN,
)
from system.models import (
    DeptInfo,
    ExportRecord,
    Menu,
    MenuMeta,
    Post,
    Tag,
    TaggedItem,
    UserInfo,
    UserRole,
)

pytestmark = pytest.mark.django_db

#: 演示角色菜单最小集：查看类（保留）+ 破坏性写（应被摘除）
SAFE_PERMISSIONS = [
    ("list:SystemUser", "GET"),
    ("retrieve:SystemUser", "GET"),
    ("exportData:SystemUser", "GET"),
    ("list:SystemRole", "GET"),
    ("list:SystemPost", "GET"),
    ("list:SystemApprovalInstance", "GET"),
]
DESTRUCTIVE_PERMISSIONS = [
    ("destroy:SystemUser", "DELETE"),
    ("batchDestroy:SystemUser", "POST"),
    ("importData:SystemUser", "POST"),
    ("resetPassword:SystemUser", "POST"),
    ("create:SystemUser", "POST"),
    ("partialUpdate:SystemUser", "PATCH"),
    ("create:SystemRole", "POST"),
    ("partialUpdate:SystemRole", "PATCH"),
    ("empower:SystemDept", "POST"),
    ("create:SystemFlower", "POST"),
    ("recyclePurge:SystemPost", "DELETE"),
]


@pytest.fixture
def demo_dept(db):
    return DeptInfo.objects.create(code="demo", name="演示部门", rank=900, is_active=True)


@pytest.fixture
def demo_role(db):
    """演示模式角色 + 一批查看类与破坏性权限点菜单。"""
    role = UserRole.objects.create(name="演示模式", code=DEMO_ROLE_CODE, is_active=True)
    menus = []
    for name, method in SAFE_PERMISSIONS + DESTRUCTIVE_PERMISSIONS:
        meta = MenuMeta.objects.create(title=name)
        menus.append(
            Menu.objects.create(
                name=name, path=f"api/{name}$", menu_type=Menu.MenuChoices.PERMISSION, method=method, meta=meta
            )
        )
    role.menu.set(menus)
    return role


@pytest.fixture
def superuser(db):
    return UserInfo.objects.create_superuser("xadmin", "xadmin@test.local", "TestPwd123!")


# ---------------------------------------------------------------- 任务一：演示账号


def test_admin_account_created_granted_and_pruned(demo_dept, demo_role, superuser):
    call_command("seed_demo_admin")

    admin = UserInfo.objects.get(username=ADMIN_USERNAME)
    assert admin.check_password(ADMIN_PASSWORD)
    assert admin.is_active and not admin.is_superuser and not admin.is_staff
    assert admin.dept_id == demo_dept.pk
    assert list(admin.roles.values_list("code", flat=True)) == [DEMO_ROLE_CODE]
    # 共享演示账号禁止绑定任何 MFA（白名单收窄为空集），防访客绑定后锁死公共登录
    assert admin.allowed_mfa_types == ["none"]

    # 角色菜单：查看类保留、破坏性写摘除
    granted = set(demo_role.menu.values_list("name", flat=True))
    for name, _ in SAFE_PERMISSIONS:
        assert name in granted
    for name, _ in DESTRUCTIVE_PERMISSIONS:
        assert name not in granted

    # 幂等：重复执行不产生重复账号
    call_command("seed_demo_admin")
    assert UserInfo.all_objects.filter(username=ADMIN_USERNAME).count() == 1


def test_clean_removes_demo_account_keeps_superuser(demo_dept, demo_role, superuser):
    call_command("seed_demo_admin")
    assert UserInfo.objects.filter(username=ADMIN_USERNAME).exists()

    call_command("seed_demo_admin", clean_only=True)
    assert not UserInfo.all_objects.filter(username=ADMIN_USERNAME, is_superuser=False).exists()
    # 超管不受影响
    assert UserInfo.objects.filter(username="xadmin", is_superuser=True).exists()


def test_seed_skips_superuser_with_same_name(db, demo_role):
    """同名真实超管绝不改写密码/权限，也不授予演示角色。"""
    real = UserInfo.objects.create_superuser("admin", "admin@corp.local", "RealSuperPwd!")
    original_hash = real.password

    call_command("seed_demo_admin")

    real.refresh_from_db()
    assert real.is_superuser
    assert real.password == original_hash  # 密码未被改成 admin123
    assert not real.roles.exists()


def test_is_destructive_rule_flags_writes():
    def make(name, method):
        return Menu(name=name, method=method, menu_type=Menu.MenuChoices.PERMISSION)

    assert is_destructive(make("destroy:SystemUser", "DELETE"))
    assert is_destructive(make("batchDestroy:SystemRole", "POST"))
    assert is_destructive(make("resetPassword:SystemUser", "POST"))
    assert is_destructive(make("create:SystemRole", "POST"))
    assert is_destructive(make("create:DataDataset", "POST"))
    assert is_destructive(make("mcp:AiMcp", "POST"))
    assert not is_destructive(make("list:SystemUser", "GET"))
    assert not is_destructive(make("approve:SystemApprovalInstance", "POST"))
    assert not is_destructive(make("execute:DataDataset", "POST"))
    assert "create:SystemRole" in DENY_PERMISSIONS


# ---------------------------------------------------------------- 任务二：补充数据


@pytest.fixture
def extras_org(db):
    """岗位/打标所需的演示组织与用户。"""
    rd = DeptInfo.objects.create(code="demo_rd", name="示例-研发部", rank=900, is_active=True)
    fin = DeptInfo.objects.create(code="demo_fin", name="示例-财务部", rank=900, is_active=True)
    for username in POST_ASSIGN:
        if username == ADMIN_USERNAME:
            continue
        UserInfo.objects.create_user(username=username, password=None)
    return rd, fin


@pytest.fixture
def builtin_tags(db):
    # 内置标签由 post_migrate 同步（system/builtin.py），测试库已存在，直接复用
    tags = []
    for name in ("重点", "待跟进", "归档"):
        tag, _created = Tag.objects.get_or_create(name=name, defaults={"builtin": True})
        tags.append(tag)
    return tags


def test_extras_creates_and_cleans_all(demo_dept, extras_org, builtin_tags, superuser):
    call_command("seed_demo_admin")
    call_command("seed_demo_extras")

    posts = Post.objects.filter(code__startswith=POST_CODE_PREFIX)
    assert posts.count() == len(POST_PLAN)
    assert posts.filter(dept__isnull=True).exists()  # 全组织通用岗
    assert posts.filter(is_active=False).exists()  # 停用岗位
    admin = UserInfo.objects.get(username=ADMIN_USERNAME)
    assert admin.posts.exists()

    assert TaggedItem.objects.filter(tag__builtin=True).count() >= 2
    assert AiChatMessage.objects.filter(extra__demo=True).count() == len(AI_MESSAGE_PLAN)
    assert ExportRecord.objects.filter(pk__in=EXPORT_PKS).count() == 3
    assert set(ExportRecord.objects.filter(pk__in=EXPORT_PKS).values_list("status", flat=True)) == {
        ExportRecord.Status.SUCCESS,
        ExportRecord.Status.RUNNING,
        ExportRecord.Status.FAILURE,
    }

    # 幂等：重复执行不重复
    call_command("seed_demo_extras")
    assert Post.all_objects.filter(code__startswith=POST_CODE_PREFIX).count() == len(POST_PLAN)
    assert AiChatMessage.objects.filter(extra__demo=True).count() == len(AI_MESSAGE_PLAN)
    assert ExportRecord.objects.filter(pk__in=EXPORT_PKS).count() == 3

    call_command("seed_demo_extras", clean_only=True)
    assert Post.all_objects.filter(code__startswith=POST_CODE_PREFIX).count() == 0
    assert TaggedItem.objects.filter(tag__builtin=True).count() == 0
    assert AiChatMessage.objects.filter(extra__demo=True).count() == 0
    assert ExportRecord.objects.filter(pk__in=EXPORT_PKS).count() == 0
    # 内置标签本体不受清理影响
    assert Tag.objects.filter(builtin=True).count() == len(builtin_tags)
