# -*- coding: utf-8 -*-
"""demo 定时任务（自动下架滞销书籍）行为测试。

口径：
- ``shared_task`` 直接调用即同步执行（不依赖 broker），测试与排查同路径；
- 只命中「已上架 + 落过上架时间 + 超过阈值」的数据；软删（回收站）数据不参与；
- ``dry_run`` 只统计不落库。
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from demo.models import Book
from demo.tasks import auto_off_shelf_books

pytestmark = pytest.mark.django_db


def _make_book(superuser, name, status, on_shelf_days_ago=None):
    book = Book.objects.create(
        name=name, isbn=f"isbn-{name}", author="a", admin=superuser, admin2=superuser, status=status
    )
    if on_shelf_days_ago is not None:
        book.on_shelf_time = timezone.now() - timedelta(days=on_shelf_days_ago)
        book.save(update_fields=["on_shelf_time"])
    return book


class TestAutoOffShelfBooks:
    def test_stale_books_taken_off_fresh_and_draft_untouched(self, superuser):
        stale = _make_book(superuser, "stale", Book.Status.ON_SHELF, on_shelf_days_ago=31)
        fresh = _make_book(superuser, "fresh", Book.Status.ON_SHELF, on_shelf_days_ago=1)
        draft = _make_book(superuser, "draft", Book.Status.DRAFT)

        detail = auto_off_shelf_books(days=30)

        stale.refresh_from_db()
        fresh.refresh_from_db()
        draft.refresh_from_db()
        assert stale.status == Book.Status.DRAFT and stale.is_active is False
        assert fresh.status == Book.Status.ON_SHELF
        assert draft.status == Book.Status.DRAFT
        assert "matched=1" in detail and "updated=1" in detail

    def test_dry_run_only_counts(self, superuser):
        stale = _make_book(superuser, "stale", Book.Status.ON_SHELF, on_shelf_days_ago=31)
        detail = auto_off_shelf_books(days=30, dry_run=True)
        stale.refresh_from_db()
        assert stale.status == Book.Status.ON_SHELF
        assert "matched=1" in detail and "updated=0" in detail

    def test_soft_deleted_books_ignored(self, superuser):
        """回收站数据不参与任务（默认管理器排除软删）。"""
        book = _make_book(superuser, "bin", Book.Status.ON_SHELF, on_shelf_days_ago=31)
        book.delete()

        detail = auto_off_shelf_books(days=30)

        assert Book.all_objects.get(pk=book.pk).status == Book.Status.ON_SHELF
        assert "matched=0" in detail
