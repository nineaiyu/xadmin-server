import pytest

from system.models import FieldPermission, ModelLabelField
from system.search import SEARCH_PROVIDERS

pytestmark = pytest.mark.django_db


def test_dbg(normal_user, role, menu_factory):
    search_menu = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET")
    list_menu = menu_factory("list:SystemUser", path="api/identity/user$", method="GET")
    role.menu.add(search_menu, list_menu)
    normal_user.roles.add(role)
    parent = ModelLabelField.objects.create(
        name="identity.userinfo", label="用户", field_type=ModelLabelField.FieldChoices.ROLE
    )
    username = ModelLabelField.objects.create(
        name="username", label="用户名", parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
    )
    FieldPermission.objects.create(role=role, menu=list_menu).field.add(username)
    provider = next(p for p in SEARCH_PROVIDERS if p.key == "user")
    from common.core.permission import get_menu_pk, get_user_permission

    data = get_user_permission(normal_user, "GET")
    print("menu_pk", get_menu_pk(data, "/api/identity/user"))
    print("allowed", provider._allowed_fields(normal_user, data))
