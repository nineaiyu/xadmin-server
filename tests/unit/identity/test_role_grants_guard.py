# -*- coding: utf-8 -*-
"""角色授权保存口径守护（编辑弹层清空授权的前端门控由列表页 hook 与 RoleForm 兜底）。

后端契约：PATCH 未提交 ``menu`` / ``fields`` 键时不得清空既有授权（未提交键不覆盖）；
显式提交 ``menu: []`` / ``fields: {}`` 仍表示整体清空（合法操作，语义不变）。
"""

import pytest

from system.models import FieldPermission, Menu

pytestmark = pytest.mark.django_db

ROLE_URL = "/api/identity/role"


def _grant_role_edit(role, menu_factory):
    from django.core.cache import cache as django_cache

    menu = menu_factory("partialUpdate:SystemRole", path="api/identity/role/(?P<pk>[^/.]+)$", method="PATCH")
    role.menu.add(menu)
    django_cache.clear()


def _existing_grant(menu_factory):
    return menu_factory("list:DemoBook", path="api/demo/book$", method="GET")


def test_patch_without_menu_key_keeps_grants(api_client, superuser, role, menu_factory):
    """PATCH 只提交 name 等非授权键：既有菜单授权必须原样保留。"""
    _grant_role_edit(role, menu_factory)
    grant = _existing_grant(menu_factory)
    role.menu.add(grant)
    api_client.force_authenticate(user=superuser)
    resp = api_client.patch(f"{ROLE_URL}/{role.pk}", {"name": "改名不碰授权"}, format="json")
    assert resp.status_code == 200
    role.refresh_from_db()
    pks = set(role.menu.values_list("pk", flat=True))
    assert grant.pk in pks


def test_patch_with_empty_menu_clears_grants(api_client, superuser, role, menu_factory):
    """显式提交 menu: [] = 整体清空（合法操作）；契约不因守护而改变。"""
    _grant_role_edit(role, menu_factory)
    role.menu.add(_existing_grant(menu_factory))
    api_client.force_authenticate(user=superuser)
    resp = api_client.patch(f"{ROLE_URL}/{role.pk}", {"menu": []}, format="json")
    assert resp.status_code == 200
    role.refresh_from_db()
    assert role.menu.count() == 0


def _field_grant(role, menu_factory):
    menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
    return FieldPermission.objects.create(role=role, menu=menu)


def test_patch_with_empty_fields_clears_field_grants(api_client, superuser, role, menu_factory):
    """显式提交 fields: {} 必须清空字段白名单（前端撤销授权的唯一通道）。"""
    _grant_role_edit(role, menu_factory)
    grant = _field_grant(role, menu_factory)
    api_client.force_authenticate(user=superuser)
    resp = api_client.patch(f"{ROLE_URL}/{role.pk}", {"fields": {}}, format="json")
    assert resp.status_code == 200
    assert not FieldPermission.objects.filter(pk=grant.pk).exists()


def test_patch_without_fields_key_keeps_field_grants(api_client, superuser, role, menu_factory):
    """未提交 fields 键（只改名称等）保留既有字段白名单。"""
    _grant_role_edit(role, menu_factory)
    grant = _field_grant(role, menu_factory)
    api_client.force_authenticate(user=superuser)
    resp = api_client.patch(f"{ROLE_URL}/{role.pk}", {"name": "改名不碰字段"}, format="json")
    assert resp.status_code == 200
    assert FieldPermission.objects.filter(pk=grant.pk).exists()
