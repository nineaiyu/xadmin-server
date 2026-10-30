# -*- coding: utf-8 -*-
"""设置页重组的升级授权回填守护。

拆分「消息通知 → 通知渠道」「安全设置 → 登录安全 + 账号安全」后，自定义角色只持有
被迁移权限点时不应在升级后丢失新页面导航；无被迁移权限点的角色不应被无差别授权。
"""

import pytest

from identity.models import UserRole
from system.management.commands.post_upgrade import Command
from system.models import Menu, MenuMeta

pytestmark = pytest.mark.django_db


def _menu(name: str, menu_type: int, path: str = "", component: str = "") -> Menu:
    meta = MenuMeta.objects.create(title=name)
    return Menu.objects.create(name=name, menu_type=menu_type, path=path, component=component, meta=meta)


def _role(code: str) -> UserRole:
    return UserRole.objects.create(name=code, code=code)


def test_channel_page_backfilled_only_for_roles_with_moved_permissions():
    old_page = _menu("SettingMessage", Menu.MenuChoices.MENU, "/settings/message", "settings/message")
    new_page = _menu("SettingChannel", Menu.MenuChoices.MENU, "/settings/channel", "settings/channel")
    moved = _menu("retrieve:ImNotifySetting", Menu.MenuChoices.PERMISSION, "api/settings/notify/im$")

    role = _role("custom-channel")
    role.menu.add(old_page, moved)
    role_without_moved = _role("custom-message-only")
    role_without_moved.menu.add(old_page)
    role_without_old_page = _role("custom-permission-only")
    role_without_old_page.menu.add(moved)

    assert Command()._backfill_regrouped_page_grants() == 1
    assert role.menu.filter(pk=new_page.pk).exists()
    assert not role_without_moved.menu.filter(pk=new_page.pk).exists()
    assert not role_without_old_page.menu.filter(pk=new_page.pk).exists()
    # 幂等：再次执行不重复计数
    assert Command()._backfill_regrouped_page_grants() == 0


def test_captcha_page_backfilled_for_moved_permissions():
    """验证码页从「安全设置」独立成页：持有图片验证码权限点的角色升级后不丢入口。"""
    old_page = _menu("SettingSecurity", Menu.MenuChoices.MENU, "/settings/security/index", "settings/security/index")
    new_page = _menu("SettingCaptcha", Menu.MenuChoices.MENU, "/settings/captcha", "settings/captcha")
    moved = _menu("retrieve:SecurityCaptchaCode", Menu.MenuChoices.PERMISSION, "api/settings/captcha$")
    moved_write = _menu("partialUpdate:SecurityCaptchaCode", Menu.MenuChoices.PERMISSION, "api/settings/captcha$")

    role = _role("custom-captcha")
    role.menu.add(old_page, moved, moved_write)
    role_without_moved = _role("custom-security-only")
    role_without_moved.menu.add(old_page)
    role_without_old_page = _role("custom-captcha-permission-only")
    role_without_old_page.menu.add(moved)

    assert Command()._backfill_regrouped_page_grants() == 1
    assert role.menu.filter(pk=new_page.pk).exists()
    assert not role_without_moved.menu.filter(pk=new_page.pk).exists()
    assert not role_without_old_page.menu.filter(pk=new_page.pk).exists()
    assert Command()._backfill_regrouped_page_grants() == 0


def test_account_security_page_backfilled_for_moved_permissions():
    old_page = _menu("SettingSecurity", Menu.MenuChoices.MENU, "/settings/security/index", "settings/security/index")
    new_page = _menu(
        "SettingAccountSecurity", Menu.MenuChoices.MENU, "/settings/account-security", "settings/account-security"
    )
    moved = _menu("retrieve:SecurityRegisterAuth", Menu.MenuChoices.PERMISSION, "api/settings/register/auth$")

    role = _role("custom-account")
    role.menu.add(old_page, moved)

    assert Command()._backfill_regrouped_page_grants() == 1
    assert role.menu.filter(pk=new_page.pk).exists()


def test_dead_permission_is_cleaned_on_upgrade():
    """旧库结构对齐路径不执行本仓库数据迁移，升级流程必须兜底清理。"""
    dead = _menu("retrieve:SystemUserLoginLog", Menu.MenuChoices.PERMISSION, "api/audit/logs/login$")
    meta_pk = dead.meta_id

    Command()._remove_dead_permissions()

    assert not Menu.objects.filter(name="retrieve:SystemUserLoginLog").exists()
    assert not MenuMeta.objects.filter(pk=meta_pk).exists()
    # 幂等：再次执行不报错也不产生副作用
    Command()._remove_dead_permissions()
    assert not Menu.objects.filter(name="retrieve:SystemUserLoginLog").exists()
