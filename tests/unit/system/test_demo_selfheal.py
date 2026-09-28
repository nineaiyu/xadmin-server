# -*- coding: utf-8 -*-
"""演示账号自愈周期任务（system.tasks::demo_account_selfheal_job）单元测试。

守护：演示环境被恶意访客改密码 / 故意输错锁死后次日自动恢复发布态；
非演示环境（无该账号）与人工下线（停用/回收站）均跳过。
"""

import pytest
from django.core.management import call_command
from django.utils import timezone

from settings.utils.security import LoginBlockUtil
from system.management.commands.seed_demo_admin import ADMIN_PASSWORD, ADMIN_USERNAME
from system.models import DeptInfo, UserInfo
from system.tasks import demo_account_selfheal_job

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_admin(db):
    """最小演示环境：演示部门 + 演示账号（seed_demo_admin 全量保证）。"""
    DeptInfo.objects.get_or_create(code="demo", defaults={"name": "演示部门", "rank": 900, "is_active": True})
    UserInfo.objects.create_superuser("xadmin", "xadmin@test.local", "TestPwd123!")
    call_command("seed_demo_admin")
    return UserInfo.objects.get(username=ADMIN_USERNAME)


def test_selfheal_restores_password_and_clears_block(demo_admin):
    # 模拟恶意场景：改掉密码 + 故意输错触发用户级锁定
    demo_admin.set_password("Hijacked@2026")
    demo_admin.save(update_fields=["password"])
    block = LoginBlockUtil(ADMIN_USERNAME, "1.2.3.4")
    for _ in range(10):
        block.incr_failed_count()
    assert LoginBlockUtil.is_user_block(ADMIN_USERNAME) is True

    result = demo_account_selfheal_job()
    assert result == ADMIN_USERNAME

    demo_admin.refresh_from_db()
    assert demo_admin.check_password(ADMIN_PASSWORD)
    assert LoginBlockUtil.is_user_block(ADMIN_USERNAME) is False


def test_selfheal_reapplies_mfa_whitelist(demo_admin):
    UserInfo.objects.filter(username=ADMIN_USERNAME).update(allowed_mfa_types=[])
    demo_account_selfheal_job()
    assert UserInfo.objects.get(username=ADMIN_USERNAME).allowed_mfa_types == ["none"]


def test_selfheal_skips_when_demo_account_absent(db):
    assert demo_account_selfheal_job() is None


def test_selfheal_skips_disabled_or_recycled(demo_admin):
    UserInfo.objects.filter(username=ADMIN_USERNAME).update(is_active=False)
    assert demo_account_selfheal_job() is None

    UserInfo.all_objects.filter(username=ADMIN_USERNAME).update(is_active=True, deleted_at=timezone.now())
    assert demo_account_selfheal_job() is None
