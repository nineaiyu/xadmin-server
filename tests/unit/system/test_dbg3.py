import json

import pytest
from system.models import Menu, MenuMeta

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree(db):
    pm = MenuMeta.objects.create(title="系统管理")
    parent = Menu.objects.create(name="系统管理", path="system", menu_type=Menu.MenuChoices.DIRECTORY, meta=pm)
    for i, name in enumerate(["用户管理", "角色管理"]):
        m = MenuMeta.objects.create(title=name)
        Menu.objects.create(name=name, path=f"api/system/{'user' if i == 0 else 'role'}$",
                            method="GET", menu_type=Menu.MenuChoices.MENU, parent=parent, meta=m)
    return parent


def test_dbg(auth_client, tree):
    r1 = auth_client.get("/api/system/routes")
    r2 = auth_client.get("/api/system/routes")

    def pl(r):
        return r.data if hasattr(r, "data") else json.loads(r.content.decode())

    a, b = pl(r1)["data"], pl(r2)["data"]
    print("\nA0 keys", list(a[0].keys()))
    print("B0 keys", list(b[0].keys()))
    print("A children", [c["name"] for c in a[0]["children"]])
    print("B children", [c["name"] for c in b[0]["children"]])
    for k in a[0]:
        if a[0][k] != b[0].get(k):
            print("DIFF", k, a[0][k], "VS", b[0].get(k))
    assert True
