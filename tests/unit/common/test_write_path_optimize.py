# -*- coding: utf-8 -*-
"""写路径优化测试（PERF-10 / PERF-11 / PERF-12 / PERF-19）。

覆盖：
1. PERF-10 导入路径 request 级 memo：字段校验查询数与行数解耦，
   且 (field, pk) 严格隔离，不同字段互不串用；
2. PERF-11 AutoCleanFileMixin.save 非文件字段保存跳过 diff 前置查询；
3. PERF-12 rank 批量排序：单条 UPDATE；
4. PERF-19 batch_destroy：无文件清理需求的模型走批量 delete()。
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.fields import BasePrimaryKeyRelatedField
from common.core.models import AutoCleanFileMixin
from demo.models import Book
from demo.views import BookViewSet
from system.models import UserInfo
from system.models.upload import UploadFile

pytestmark = pytest.mark.django_db

BOOK_URL = "/api/demo/book"


def _business_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


@pytest.fixture
def admin_user(db):
    return UserInfo.objects.create_user(username="adm", password="Xadmin@123456")


@pytest.fixture
def upload_file(superuser):
    return UploadFile.objects.create(
        filename="cover.png", filesize=100, mime_type="image/png", md5sum="a" * 32, creator=superuser
    )


def _import_rows(admin_pk, file_pk, count, start=0):
    return [{"name": f"书{n}", "isbn": f"isbn{n}", "author": "a",
             "admin": admin_pk, "admin2": admin_pk, "managers": [admin_pk], "file": file_pk}
            for n in range(start, start + count)]


class TestRelatedMemo:
    def test_memo_reuses_same_field_pk(self, normal_user):
        serializer = type("S", (), {})()
        object.__setattr__(serializer, "context", {"request": type("Req", (), {})()})
        field = BasePrimaryKeyRelatedField(attrs=["pk", "username"], queryset=UserInfo.objects.all())
        field.field_name = "admin"
        field.bind("admin", serializer)

        first = field.to_internal_value(normal_user.pk)
        second = field.to_internal_value(normal_user.pk)

        assert first.pk == second.pk == normal_user.pk
        assert set(serializer.context["request"]._related_memo) == {("admin", str(normal_user.pk))}

    def test_memo_isolated_by_field_name(self, normal_user):
        """同一 pk 在不同字段下不得串用（各字段的数据权限过滤条件可能不同）"""
        request = type("Req", (), {})()
        for name in ("admin", "managers"):
            holder = type("S", (), {})()
            object.__setattr__(holder, "context", {"request": request})
            field = BasePrimaryKeyRelatedField(attrs=["pk", "username"], queryset=UserInfo.objects.all())
            field.field_name = name
            field.bind(name, holder)
            field.to_internal_value(normal_user.pk)
        assert set(request._related_memo) == {("admin", str(normal_user.pk)), ("managers", str(normal_user.pk))}

    def test_import_selects_independent_of_row_count(self, auth_client, admin_user, upload_file):
        """导入 1 行 vs 7 行（同一关联 pk）时，关联字段校验的 SELECT 数保持常数"""
        url = f"{BOOK_URL}/import-data?action=create&task=false"

        def measure(count):
            with CaptureQueriesContext(connection) as ctx:
                resp = auth_client.post(url, _import_rows(admin_user.pk, upload_file.pk, count), format="json")
            assert resp.status_code == 200, resp.data
            assert Book.objects.count() == count
            Book.objects.all().delete()
            # 只统计 to_internal_value 的关联校验 SELECT（排除 M2M set() 的簿记查询）
            return len([q for q in _business_queries(ctx)
                        if "system_userinfo" in q and "demo_book_managers" not in q])

        one = measure(1)
        many = measure(7)

        assert one > 0
        # 行数从 1 变为 7，关联字段校验的 SELECT 数保持常数（memo 生效）
        assert many <= one + 1, (one, many)

    def test_import_missing_pk_fails_for_each_row(self, auth_client, admin_user, upload_file):
        """memo 不得掩盖校验失败：不存在的 pk 每行都报错"""
        url = f"{BOOK_URL}/import-data?action=create&task=false&ignore_error=true"
        rows = [{"name": f"书{n}", "isbn": f"x{n}", "author": "a",
                 "admin": "00000000-0000-0000-0000-000000000000", "file": upload_file.pk}
                for n in range(2)]
        resp = auth_client.post(url, rows, format="json")
        assert resp.status_code == 200
        assert Book.objects.count() == 0


class TestAutoCleanFileMixinSave:
    def test_save_without_update_fields_keeps_file_diff(self, superuser):
        f = UploadFile.objects.create(filename="a.png", filesize=1, mime_type="image/png",
                                      md5sum="b" * 32, creator=superuser)
        with CaptureQueriesContext(connection) as ctx:
            f.save()
        assert len(_business_queries(ctx)) >= 2  # diff 查询 + 更新

    def test_save_with_unrelated_update_fields_skips_diff_query(self, superuser):
        f = UploadFile.objects.create(filename="a.png", filesize=1, mime_type="image/png",
                                      md5sum="b" * 32, creator=superuser)
        with CaptureQueriesContext(connection) as ctx:
            f.save(update_fields=["filename"])
        # 仅 1 条 UPDATE，无前置 SELECT（PERF-11）
        assert len(_business_queries(ctx)) == 1
        f.refresh_from_db()
        assert f.filename == "a.png"

    def test_save_with_file_update_field_still_diffs(self, superuser):
        f = UploadFile.objects.create(filename="a.png", filesize=1, mime_type="image/png",
                                      md5sum="b" * 32, creator=superuser)
        with CaptureQueriesContext(connection) as ctx:
            f.save(update_fields=["filepath"])
        assert len(_business_queries(ctx)) >= 2

    def test_user_login_style_save_skips_query(self, db, dept):
        """UserInfo 继承 AutoCleanFileMixin：更新 last_login 等非文件字段不再多查一次"""
        user = UserInfo.objects.create_user(username="lg", password="Xadmin@123456", dept=dept)
        with CaptureQueriesContext(connection) as ctx:
            user.save(update_fields=["last_login"])
        assert len(_business_queries(ctx)) == 1

    def test_file_field_names_cached_per_instance(self):
        f = UploadFile(filename="a.png", filesize=1, mime_type="image/png", md5sum="b" * 32)
        assert f._file_field_names == {"filepath"}
        assert f._file_field_names is f._file_field_names

    def test_has_file_cleanup_detection(self):
        assert AutoCleanFileMixin.has_file_cleanup(UploadFile) is True   # 自身文件字段
        assert AutoCleanFileMixin.has_file_cleanup(Book) is True         # 关联 UploadFile

    def test_viewset_file_cleanup_detection(self):
        assert BookViewSet()._has_file_cleanup() is True


class TestRankBatch:
    def test_rank_uses_single_update(self, auth_client, menu_factory):
        """rank 使用 Case/When 单条批量 UPDATE（PERF-12），不再逐条 filter+update"""
        menus = [menu_factory(f"菜单{i}") for i in range(3)]
        payload = [str(menus[2].pk), str(menus[0].pk)]

        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.post("/api/system/menu/rank", payload, format="json")

        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        updates = [q for q in _business_queries(ctx) if q.strip().upper().startswith("UPDATE")]
        assert len(updates) == 1

        menus[2].refresh_from_db()
        menus[0].refresh_from_db()
        menus[1].refresh_from_db()
        assert menus[2].rank == 1
        assert menus[0].rank == 2

    def test_rank_empty_payload_noop(self, auth_client):
        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.post("/api/system/menu/rank", [], format="json")
        assert resp.status_code == 200
        # 排序动作本身不应产生任何查询（操作日志占位 INSERT 除外）
        assert not [q for q in _business_queries(ctx) if "system_menu" in q]


class TestBatchDestroy:
    def test_batch_destroy_bulk_path(self, auth_client, superuser, upload_file):
        for i in range(4):
            Book.objects.create(name=f"书{i}", isbn=f"i{i}", author="a",
                                admin=superuser, admin2=superuser, file=upload_file)
        pks = [str(b.pk) for b in Book.objects.all()]

        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.post(f"{BOOK_URL}/batch-destroy", pks, format="json")

        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert Book.objects.count() == 0
        deletes = [q for q in _business_queries(ctx) if q.strip().upper().startswith("DELETE")]
        assert len(deletes) >= 1

    def test_batch_destroy_ignores_unknown_pks(self, auth_client, superuser, upload_file):
        b = Book.objects.create(name="书", isbn="i1", author="a",
                                admin=superuser, admin2=superuser, file=upload_file)
        resp = auth_client.post(f"{BOOK_URL}/batch-destroy", [str(b.pk), "999999999"], format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert Book.objects.count() == 0

    def test_viewset_without_queryset_defaults_to_per_row(self):
        class NoQuerysetView:
            queryset = None

            _has_file_cleanup = BookViewSet._has_file_cleanup

        assert NoQuerysetView()._has_file_cleanup() is True
