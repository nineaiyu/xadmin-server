# -*- coding: utf-8 -*-
"""目录软删的缓存失效面：级联 update() 不触发 post_save，后代权限缓存必须显式失效。"""

import pytest

from system.models import Menu

pytestmark = pytest.mark.django_db


def test_delete_invalidates_descendant_user_caches(menu_factory, monkeypatch, django_capture_on_commit_callbacks):
    parent = menu_factory("cascade-parent", menu_type=Menu.MenuChoices.DIRECTORY)
    child = menu_factory("cascade-child", parent=parent)
    grandchild = menu_factory("cascade-grandchild", parent=child)
    invalidated = []
    # Menu.delete 的后代失效直接取定义模块（identity.services 惰性再导出类型不可调用）
    monkeypatch.setattr(
        "identity.signal_handler.invalidate_menu_user_caches",
        lambda menus: invalidated.extend(menu.pk for menu in menus),
    )

    with django_capture_on_commit_callbacks(execute=True):
        parent.delete()

    assert child.pk in invalidated
    assert grandchild.pk in invalidated
