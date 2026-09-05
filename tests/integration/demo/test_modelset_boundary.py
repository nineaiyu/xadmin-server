# -*- coding: utf-8 -*-
"""BaseModelSet 组合 Action 的接口级边界测试（T2.1 拆分护航）。

以 demo BookViewSet 为载体，覆盖既有测试未触达的组合 Action 路径：
- ChoicesAction.choices_dict：choices 字段聚合（BookViewSet 未混入，用组合视图直调）
- UploadFileAction.upload：合法图片上传 / 非法类型拒绝（同上）
- ImportExportDataAction.import_data：同步 create / update / ignore_error / 失败回滚
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from demo.models import Book

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


@pytest.fixture
def upload_file(superuser):
    from system.models import UploadFile

    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


@pytest.fixture
def book(superuser, upload_file):
    return Book.objects.create(
        name="边界书", isbn="isbn-1", author="作者", admin=superuser, admin2=superuser, file=upload_file
    )


@pytest.fixture
def png_file():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile("edge.png", buf.getvalue(), content_type="image/png")


def _call(viewset_cls, action_map, method, path, user, data=None, fmt="json", **kwargs):
    """绕过 URL 注册，直接实例化 as_view 调用指定 action。"""
    factory = APIRequestFactory()
    request = getattr(factory, method)(path, data=data, format=fmt)
    force_authenticate(request, user=user)
    return viewset_cls.as_view(action_map)(request, **kwargs)


@pytest.fixture
def _patch_upload_size(monkeypatch):
    """上传大小上限固定为 1MB，避免依赖 SysConfig 的运行时配置。"""
    from common.core.modelset import UploadFileAction

    monkeypatch.setattr(UploadFileAction, "get_upload_size", lambda self: 1024 * 1024)


@pytest.mark.usefixtures("_patch_upload_size")
class TestUploadAction:
    def test_upload_image_updates_avatar(self, superuser, book, png_file):
        from common.core.modelset import UploadFileAction
        from demo.views import BookViewSet

        class BookUploadViewSet(BookViewSet, UploadFileAction):
            pass

        resp = _call(
            BookUploadViewSet,
            {"post": "upload"},
            "post",
            f"{BOOK_LIST_URL}/{book.pk}/upload",
            superuser,
            data={"file": png_file},
            fmt="multipart",
            pk=str(book.pk),
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.avatar

    def test_upload_rejects_wrong_type(self, superuser, book):
        from common.core.modelset import UploadFileAction
        from demo.views import BookViewSet

        class BookUploadViewSet(BookViewSet, UploadFileAction):
            pass

        bad = SimpleUploadedFile("evil.txt", b"not-an-image", content_type="text/plain")
        resp = _call(
            BookUploadViewSet,
            {"post": "upload"},
            "post",
            f"{BOOK_LIST_URL}/{book.pk}/upload",
            superuser,
            data={"file": bad},
            fmt="multipart",
            pk=str(book.pk),
        )
        assert resp.data["code"] == 1002


class TestChoicesAction:
    def test_choices_dict_contains_model_choices(self, superuser):
        from common.core.modelset import ChoicesAction
        from demo.views import BookViewSet

        class BookChoicesViewSet(BookViewSet, ChoicesAction):
            pass

        resp = _call(BookChoicesViewSet, {"get": "choices_dict"}, "get", f"{BOOK_LIST_URL}/choices", superuser)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        category = resp.data["choices_dict"]["category"]
        labels = {item["label"] for item in category}
        assert {"小说", "文学", "哲学"} <= labels


class TestImportDataAction:
    @pytest.fixture
    def import_row(self, superuser, upload_file):
        return {
            "name": "导入书",
            "isbn": "imp-1",
            "author": "作者",
            "admin": superuser.pk,
            "admin2": superuser.pk,
            "managers": [superuser.pk],
            "file": upload_file.pk,
        }

    def _import(self, auth_client, rows, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return auth_client.post(f"{BOOK_LIST_URL}/import-data?task=false&{query}", rows, format="json")

    def test_sync_create(self, auth_client, import_row):
        rows = [dict(import_row, name=f"导入书{i}", isbn=f"imp-{i}") for i in range(2)]
        resp = self._import(auth_client, rows, action="create")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert Book.objects.count() == 2

    def test_sync_update(self, auth_client, book):
        resp = self._import(auth_client, [{"pk": book.pk, "name": "导入改名"}], action="update")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        book.refresh_from_db()
        assert book.name == "导入改名"

    def test_ignore_error_skips_invalid_rows(self, auth_client, import_row):
        rows = [import_row, {"name": "缺管理员的书", "isbn": "bad-1"}]
        resp = self._import(auth_client, rows, action="create", ignore_error="true")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert Book.objects.count() == 1
        assert Book.objects.filter(name="导入书").exists()

    def test_invalid_row_rolls_back_whole_import(self, auth_client, import_row):
        """无 ignore_error 时校验失败抛出，事务整体回滚（含此前已成功的行）。"""
        rows = [import_row, {"name": "无效书", "isbn": "bad-2"}]
        resp = self._import(auth_client, rows, action="create")
        assert resp.data["code"] != 1000
        assert Book.objects.count() == 0

    def test_update_missing_pk_row_is_skipped(self, auth_client):
        resp = self._import(auth_client, [{"pk": 999999, "name": "不存在"}], action="update")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000

    def test_without_action_param_fails(self, auth_client, import_row):
        resp = self._import(auth_client, [dict(import_row, isbn="no-act")])
        assert resp.data["code"] == 1001


class TestInlineMetadata:
    """T3.2：with_meta=1 内联元数据，首开合并请求。"""

    def test_list_with_meta_includes_both_metadata(self, auth_client):
        resp = auth_client.get(f"{BOOK_LIST_URL}?with_meta=1")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        columns = resp.data["data"]["search_columns"]
        fields = resp.data["data"]["search_fields"]
        assert {"name", "isbn", "admin"} <= {c["key"] for c in columns}
        assert "name" in {f["key"] for f in fields}

    def test_list_without_meta_stays_clean(self, auth_client):
        resp = auth_client.get(BOOK_LIST_URL)
        assert resp.data["code"] == 1000
        assert "search_columns" not in resp.data["data"]
        assert "search_fields" not in resp.data["data"]

    def test_list_with_meta_degrades_without_metadata_actions(self, superuser):
        """未混入元数据 Action 的视图集：不报错、不注入键。"""
        from rest_framework.viewsets import GenericViewSet

        from common.core.modelset import BaseViewSet, ListAction
        from demo.models import Book
        from demo.serializers.book import BookSerializer
        from tests.integration.demo.test_modelset_boundary import _call

        class BareListSet(BaseViewSet, ListAction, GenericViewSet):
            queryset = Book.objects.all()
            serializer_class = BookSerializer

        resp = _call(
            BareListSet, {"get": "list"}, "get", f"{BOOK_LIST_URL}?with_meta=1",
            superuser,
        )
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert "search_columns" not in resp.data["data"]
        assert "search_fields" not in resp.data["data"]
