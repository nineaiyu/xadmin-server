# -*- coding: utf-8 -*-
"""UploadFile 关联字段写路径守护测试（BaseModelSerializer.create/update）。

背景：可空外键（如 demo.Book.file）允许显式传 null（清空附件），且旧值同样可能为空。
原 update 实现直接取 ``o_file_obj.pk != n_file_obj.pk``，两侧任一为 None 即 500
（2026-09-20 测试服 PATCH /api/demo/book/3 编辑弹窗实测）；create 显式传 null 同样
会让 ``_mark_upload_files_used`` 拿到 None 崩溃。此处覆盖：

1. 旧有值 + 传 null（清空附件）→ 旧记录软删、字段置空；
2. 旧为空 + 传 null → 原样置空不崩溃；
3. 旧为空 + 传新 pk → 关联新文件并转正式态（is_tmp=False）；
4. 旧有值 + 传同一 pk → 无副作用（旧记录不删）；
5. 多附件（M2M）传空数组 → 清空旧关联（null 由 DRF 校验层标准拒绝，非 500）；
6. create 显式传 file: null → 正常创建。
"""

import pytest

from demo.models import Book
from system.models import UserInfo
from system.models.upload import UploadFile

pytestmark = pytest.mark.django_db

BOOK_URL = "/api/demo/book"


@pytest.fixture
def admin_user(db):
    return UserInfo.objects.create_user(username="adm", password="Xadmin@123456")


def _upload(superuser, name):
    return UploadFile.objects.create(filename=name, filesize=100, mime_type="image/png", md5sum=name, creator=superuser)


def _create_book(client, admin_user, **extra):
    payload = {
        "name": "书",
        "isbn": "isbn-1",
        "author": "a",
        "admin": admin_user.pk,
        "admin2": admin_user.pk,
        "managers": [admin_user.pk],
        **extra,
    }
    resp = client.post(BOOK_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    return Book.objects.get(name="书", isbn="isbn-1")


def _patch(client, book, **extra):
    resp = client.patch(f"{BOOK_URL}/{book.pk}", extra, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    book.refresh_from_db()
    return resp


class TestSingleFileField:
    def test_clear_file_soft_deletes_old_record(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "a.png")
        book = _create_book(auth_client, admin_user, file=f1.pk)

        _patch(auth_client, book, file=None)

        assert book.file is None
        assert not UploadFile.objects.filter(pk=f1.pk).exists()
        assert UploadFile.all_objects.filter(pk=f1.pk, deleted_at__isnull=False).exists()

    def test_clear_file_when_already_empty(self, auth_client, admin_user):
        book = _create_book(auth_client, admin_user)

        _patch(auth_client, book, file=None)

        assert book.file is None

    def test_set_file_from_empty_marks_used(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "b.png")
        book = _create_book(auth_client, admin_user)

        _patch(auth_client, book, file=f1.pk)

        assert book.file == f1
        f1.refresh_from_db()
        assert f1.is_tmp is False
        assert f1.deleted_at is None

    def test_set_same_file_keeps_record(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "c.png")
        book = _create_book(auth_client, admin_user, file=f1.pk)

        _patch(auth_client, book, name="书改")

        assert book.file == f1
        assert book.name == "书改"
        assert UploadFile.objects.filter(pk=f1.pk).exists()

    def test_replace_file_soft_deletes_old(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "old.png")
        f2 = _upload(superuser, "new.png")
        book = _create_book(auth_client, admin_user, file=f1.pk)

        _patch(auth_client, book, file=f2.pk)

        assert book.file == f2
        assert not UploadFile.objects.filter(pk=f1.pk).exists()
        assert UploadFile.objects.filter(pk=f2.pk).exists()


class TestMultiFileField:
    def test_clear_files_with_empty_list(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "m2.png")
        book = _create_book(auth_client, admin_user)
        book.files.add(f1)

        _patch(auth_client, book, files=[])

        assert book.files.count() == 0
        assert not UploadFile.objects.filter(pk=f1.pk).exists()

    def test_replace_files_keeps_selected(self, auth_client, superuser, admin_user):
        f1 = _upload(superuser, "m3.png")
        f2 = _upload(superuser, "m4.png")
        book = _create_book(auth_client, admin_user)
        book.files.add(f1)

        _patch(auth_client, book, files=[f2.pk])

        assert list(book.files.values_list("pk", flat=True)) == [f2.pk]
        assert not UploadFile.objects.filter(pk=f1.pk).exists()


class TestCreateWithNull:
    def test_create_with_file_null(self, auth_client, admin_user):
        book = _create_book(auth_client, admin_user, file=None)

        assert book.file is None
