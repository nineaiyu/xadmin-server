# -*- coding: utf-8 -*-
"""AI 动作参数：确认卡片展示值在执行期的稳定解析（改名/重名不改变已确认对象）。"""

import pytest

from ai.services.actions.ai_api_actions import api_action
from ai.services.actions.ai_api_params import resolve_api_params
from identity.models import UserInfo, UserRole
from system.models import Menu, MenuMeta

pytestmark = pytest.mark.django_db


def _make_menu(name):
    meta = MenuMeta.objects.create(title=name)
    return Menu.objects.create(name=name, path=f"/{name}", menu_type=Menu.MenuChoices.DIRECTORY, meta=meta)


def _spec():
    return api_action(
        "stability.demo",
        "稳定性示例",
        "",
        "POST",
        "/api/demo",
        {
            "user": {"type": "user"},
            "role": {"type": "role"},
            "menus": {"type": "menu"},
        },
    )


def _display_params(spec, user, params):
    clean, error = spec.validate(user, params)
    assert error is None, error
    return clean


class TestDisplayValueResolution:
    def test_renamed_user_and_role_still_resolve_to_confirmed_pk(self):
        user = UserInfo.objects.create_user(username="stab_user", password="Test@123456", nickname="稳定用户")
        role = UserRole.objects.create(name="稳定角色", code="stab-role")
        spec = _spec()
        clean = _display_params(spec, user, {"user": "stab_user", "role": "稳定角色"})

        # 确认后对象被改名：名称解析已不可达，但执行期仍命中确认时登记的主键
        user.username = "stab_user_renamed"
        user.nickname = "改名后用户"
        user.save(update_fields=["username", "nickname"])
        role.name = "改名后角色"
        role.save(update_fields=["name"])

        path_params, body, _query, error = resolve_api_params(spec, user, clean)
        assert error is None, error
        assert body["user"] == str(user.pk)
        assert body["role"] == str(role.pk)

    def test_renamed_menu_keeps_confirmed_subtree_pks(self):
        menu = _make_menu("stab-menu")
        child = _make_menu("stab-menu-child")
        child.parent = menu
        child.save(update_fields=["parent"])
        spec = _spec()
        user = UserInfo.objects.create_user(username="stab_admin", password="Test@123456")
        clean = _display_params(spec, user, {"menus": [menu.pk]})

        menu.name = "stab-menu-renamed"
        menu.save(update_fields=["name"])
        path_params, body, _query, error = resolve_api_params(spec, user, clean)
        assert error is None, error
        assert body["menus"] == [str(menu.pk), str(child.pk)]

    def test_deleted_object_falls_back_to_readable_error(self):
        """缓存命中但对象已删除时回落名称解析，给出可读错误而不是静默操作。"""
        user = UserInfo.objects.create_user(username="stab_gone", password="Test@123456")
        spec = _spec()
        clean = _display_params(spec, user, {"user": "stab_gone"})
        user.delete()

        _path_params, _body, _query, error = resolve_api_params(spec, user, clean)
        assert error and "stab_gone" in error
