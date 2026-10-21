# -*- coding: utf-8 -*-
"""角色授权预览的数据权限回归：list 绑定的行范围必须覆盖 preview 动作。"""

import pytest
from django.core.cache import cache as django_cache

from identity.models import UserRole
from system.models import DataPermission

pytestmark = pytest.mark.django_db

ROLE_URL = "/api/identity/role"


def _role_preview_menu(menu_factory, action):
    if action == "list":
        return menu_factory("list:SystemRole", path="api/identity/role$", method="GET")
    return menu_factory(
        "preview:SystemRole",
        path=r"api/identity/role/(?P<pk>[^/.]+)/preview$",
        method="GET",
    )


class TestRolePreviewDataScope:
    def test_preview_inherits_list_scope(self, api_client, normal_user, role, menu_factory):
        """绑定 list 的数据授权应让同资源 preview 通过，而不是 400/空集。"""
        list_menu = _role_preview_menu(menu_factory, "list")
        preview_menu = _role_preview_menu(menu_factory, "preview")
        role.menu.add(list_menu, preview_menu)

        target = UserRole.objects.create(name="预览目标", code="preview_target")
        grant = DataPermission.objects.create(
            name="角色列表全部数据",
            rules=[{"table": "identity.userrole", "field": "*", "type": "value.all", "value": "*", "match": "all"}],
        )
        grant.menu.add(list_menu)
        normal_user.rules.add(grant)
        django_cache.clear()

        api_client.force_authenticate(user=normal_user)
        response = api_client.get(f"{ROLE_URL}/{target.pk}/preview")

        assert response.status_code == 200, response.data
        assert response.data["code"] == 1000, response.data
        assert response.data["data"]["role"]["pk"] == str(target.pk)

    def test_preview_denied_without_resource_scope(self, api_client, normal_user, role, menu_factory):
        """未绑定该资源数据授权时仍 fail-closed（不因继承改动而放大）。"""
        preview_menu = _role_preview_menu(menu_factory, "preview")
        other_list = menu_factory("list:Other", path="api/demo/other$", method="GET")
        role.menu.add(preview_menu)

        target = UserRole.objects.create(name="预览目标2", code="preview_target2")
        grant = DataPermission.objects.create(
            name="其它资源全部数据",
            rules=[{"table": "identity.userrole", "field": "*", "type": "value.all", "value": "*", "match": "all"}],
        )
        grant.menu.add(other_list)
        normal_user.rules.add(grant)
        django_cache.clear()

        api_client.force_authenticate(user=normal_user)
        response = api_client.get(f"{ROLE_URL}/{target.pk}/preview")

        assert response.status_code == 400, response.data

    def test_preview_explicit_scope_overrides_list_scope(self, api_client, normal_user, role, menu_factory):
        """显式绑定 preview 的规则覆盖 list：list 可见但 preview 规则不匹配的对象应 400。"""
        list_menu = _role_preview_menu(menu_factory, "list")
        preview_menu = _role_preview_menu(menu_factory, "preview")
        role.menu.add(list_menu, preview_menu)

        target = UserRole.objects.create(name="预览目标", code="preview_target")
        other = UserRole.objects.create(name="列表可见角色", code="list_only_target")
        list_grant = DataPermission.objects.create(
            name="列表全部数据",
            rules=[{"table": "identity.userrole", "field": "*", "type": "value.all", "value": "*", "match": "all"}],
        )
        list_grant.menu.add(list_menu)
        preview_grant = DataPermission.objects.create(
            name="预览仅目标角色",
            rules=[
                {
                    "table": "identity.userrole",
                    "field": "name",
                    "type": "value.text",
                    "value": "预览目标",
                    "match": "exact",
                }
            ],
        )
        preview_grant.menu.add(preview_menu)
        normal_user.rules.add(list_grant, preview_grant)
        django_cache.clear()

        api_client.force_authenticate(user=normal_user)
        response = api_client.get(f"{ROLE_URL}/{target.pk}/preview")
        assert response.status_code == 200, response.data

        denied = api_client.get(f"{ROLE_URL}/{other.pk}/preview")
        assert denied.status_code == 400, denied.data
