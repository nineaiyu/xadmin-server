# -*- coding: utf-8 -*-
"""BaseModelSet 通用 CRUD / 导出 / 元数据 冒烟测试（以 demo.BookViewSet 为载体）。"""
import pytest

from demo.models import Book
from system.models import DataPermission

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


@pytest.fixture
def upload_file(superuser):
    from system.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


@pytest.fixture
def book_payload(superuser, upload_file):
    return {
        "name": "测试书籍",
        "isbn": "978-7-111-11111-1",
        "author": "张三",
        "category": 0,
        "price": 59.9,
        "admin": superuser.pk,
        "admin2": superuser.pk,
        "managers": [superuser.pk],
        "file": upload_file.pk,
    }


class TestBookCrudSmoke:
    def test_create_list_retrieve_update_delete(self, auth_client, book_payload):
        resp = auth_client.post(BOOK_LIST_URL, book_payload, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        pk = resp.data["data"]["pk"]
        assert Book.objects.filter(pk=pk).exists()

        resp = auth_client.get(BOOK_LIST_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["name"] == "测试书籍"

        resp = auth_client.get(f"{BOOK_LIST_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["pk"] == pk

        resp = auth_client.patch(f"{BOOK_LIST_URL}/{pk}", {"name": "改名书籍"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["name"] == "改名书籍"

        resp = auth_client.delete(f"{BOOK_LIST_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not Book.objects.filter(pk=pk).exists()

    def test_create_validation_error(self, auth_client, book_payload):
        """缺少必填的 admin 字段时应返回失败。"""
        payload = {k: v for k, v in book_payload.items() if k != "admin"}
        resp = auth_client.post(BOOK_LIST_URL, payload, format="json")
        assert resp.data["code"] != 1000

    def test_batch_destroy(self, auth_client, superuser, upload_file):
        pks = [
            Book.objects.create(
                name=f"书{i}", isbn=f"i{i}", author="a", admin=superuser, admin2=superuser, file=upload_file
            ).pk
            for i in range(2)
        ]
        resp = auth_client.post(f"{BOOK_LIST_URL}/batch-destroy", pks, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert Book.objects.count() == 0

    def test_search_filter_by_name(self, auth_client, superuser, upload_file):
        Book.objects.create(name="Python入门", isbn="i1", author="a", admin=superuser, admin2=superuser, file=upload_file)
        Book.objects.create(name="Go进阶", isbn="i2", author="a", admin=superuser, admin2=superuser, file=upload_file)
        resp = auth_client.get(BOOK_LIST_URL, {"name": "Python"})
        assert resp.data["data"]["total"] == 1
        assert resp.data["data"]["results"][0]["name"] == "Python入门"


class TestBookMetadataSmoke:
    def test_search_columns(self, auth_client):
        resp = auth_client.get(f"{BOOK_LIST_URL}/search-columns")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        keys = {item["key"] for item in resp.data["data"]}
        assert {"name", "isbn", "admin"} <= keys

    def test_search_fields(self, auth_client):
        resp = auth_client.get(f"{BOOK_LIST_URL}/search-fields")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        keys = {item["key"] for item in resp.data["data"]}
        assert "name" in keys


class TestBookExportSmoke:
    def test_export_xlsx(self, auth_client, superuser, upload_file):
        Book.objects.create(name="导出书", isbn="i9", author="a", admin=superuser, admin2=superuser, file=upload_file)
        resp = auth_client.get(f"{BOOK_LIST_URL}/export-data?type=xlsx")
        assert resp.status_code == 200
        assert len(resp.content) > 0


class TestBookDataPermissionIntegration:
    def test_non_owner_user_only_sees_own_books(self, api_client, normal_user, role, menu_factory, superuser, upload_file):
        """接口权限 + 数据权限 + 字段权限联动：普通用户只能看到自己名下的书籍。"""
        from system.models import FieldPermission, ModelLabelField

        menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        role.menu.add(menu)
        dp = DataPermission.objects.create(
            name="own", rules=[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}]
        )
        normal_user.rules.add(dp)

        # 普通用户未配置字段权限时输出为空（框架默认行为），此处显式授权 pk/name/isbn
        model_field = ModelLabelField.objects.create(
            name="demo.book", label="书籍", field_type=ModelLabelField.FieldChoices.ROLE
        )
        children = [
            ModelLabelField.objects.create(name=n, label=n, parent=model_field, field_type=ModelLabelField.FieldChoices.ROLE)
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
        results = resp.data["data"]["results"]
        assert [b["pk"] for b in results] == [my_book.pk]
        assert set(results[0].keys()) == {"pk", "name", "isbn"}