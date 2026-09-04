# -*- coding: utf-8 -*-
"""common/core/permission.py 菜单/接口权限 与 数据权限范围（HTTP 级）单元测试。

创建菜单权限 + 数据权限规则 + 字段权限，验证普通用户访问受保护接口的行为。
注意：权限结果按用户以 24h 缓存（MagicCacheData），因此"无权限->403"和
"有权限->200"必须在不同的测试方法中验证，以免同方法内缓存污染。
"""
import pytest

from demo.models import Book
from system.models import DataPermission, FieldPermission, ModelLabelField

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


@pytest.fixture
def upload_file(superuser):
    from system.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


def grant_list_permission(role, menu_factory):
    menu = menu_factory("p-list", path="api/demo/book$", method="GET")
    role.menu.add(menu)
    return menu


class TestMenuPermissionHttp:
    def test_unauthenticated_returns_401(self, api_client):
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 401
        assert resp.data["code"] == 401

    def test_with_menu_permission_returns_200(self, api_client, normal_user, role, menu_factory):
        """普通用户绑定菜单权限后可访问接口（未配置数据权限时结果为空但仍 200）。"""
        grant_list_permission(role, menu_factory)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert resp.data["data"]["total"] == 0

    def test_without_menu_permission_returns_403(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 403
        assert resp.data["code"] == 403

    def test_method_mismatch_denied(self, api_client, normal_user, role, menu_factory):
        """仅授予 GET 菜单权限，POST 请求应被拒绝。"""
        grant_list_permission(role, menu_factory)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(BOOK_LIST_URL, {}, format="json")
        assert resp.status_code == 403
        assert resp.data["code"] == 403


class TestDataPermissionHttpScope:
    def test_user_sees_only_own_records(self, api_client, normal_user, role, menu_factory, superuser, upload_file):
        """接口权限 + 数据权限 + 字段权限联动：普通用户只能看到自己名下的书籍。"""
        menu = grant_list_permission(role, menu_factory)

        dp = DataPermission.objects.create(
            name="own",
            rules=[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}],
        )
        normal_user.rules.add(dp)

        model_field = ModelLabelField.objects.create(
            name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
        )
        children = [
            ModelLabelField.objects.create(
                name=n, label=n, parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE
            )
            for n in ["pk", "name", "isbn"]
        ]
        fp = FieldPermission.objects.create(role=role, menu=menu)
        fp.field.add(*children)

        Book.objects.create(name="别人的书", isbn="i1", author="a", admin=superuser, admin2=superuser, file=upload_file)
        my_book = Book.objects.create(
            name="我的书", isbn="i2", author="a", admin=normal_user, admin2=normal_user, file=upload_file
        )

        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
        results = resp.data["data"]["results"]
        assert results[0]["pk"] == my_book.pk
        # 字段权限裁剪：仅暴露授权的 pk/name/isbn
        assert set(results[0].keys()) == {"pk", "name", "isbn"}