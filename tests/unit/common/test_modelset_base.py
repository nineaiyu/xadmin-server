# -*- coding: utf-8 -*-
"""BaseViewSet / 组合 Action 的边界单测（T2.1 拆分护航）。

覆盖 modelset 核心装配行为，这些行为在拆分重构前后必须保持一致：
- action 级 serializer 选择（{action}_serializer_class）
- 导出接口绕过分页
- select_related / prefetch_related 自动推断开关
- 响应缓存 key 规则与 invalid_cache
- run_view_by_celery_task 的同步直执分支
"""

import pytest
from django.test import RequestFactory
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from common.base.magic import cache_response
from common.core.modelset import (
    BaseModelSet,
    CacheDetailResponseMixin,
    CacheListResponseMixin,
    run_view_by_celery_task,
)
from demo.models import Book
from demo.serializers.book import BookSerializer
from demo.views import BookViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture
def view():
    view = BookViewSet()
    view.action = "list"
    return view


class TestActionSerializerClass:
    def test_action_specific_serializer_wins(self):
        class AltSerializer(BookSerializer):
            pass

        class TestViewSet(BaseModelSet):
            queryset = Book.objects.all()
            serializer_class = BookSerializer
            list_serializer_class = AltSerializer

        view = TestViewSet()
        view.action = "list"
        assert view.get_serializer_class() is AltSerializer

    def test_fallback_to_default_serializer(self):
        class TestViewSet(BaseModelSet):
            queryset = Book.objects.all()
            serializer_class = BookSerializer

        view = TestViewSet()
        view.action = "retrieve"
        assert view.get_serializer_class() is BookSerializer


class TestPaginateExportBypass:
    def _request(self, path, params=None):
        http_request = APIRequestFactory().get(path, params)
        return Request(http_request)

    def test_export_data_bypasses_pagination(self, view):
        view.action = "export_data"
        view.request = self._request("/api/demo/book/export-data", {"type": "xlsx"})
        assert view.paginate_queryset(Book.objects.none()) is None

    def test_list_request_still_paginates(self, view):
        view.request = self._request("/api/demo/book")
        page = view.paginate_queryset(Book.objects.none())
        assert page is not None


class TestOptimizeQueryset:
    def test_auto_infer_on_list_action(self, view):
        qs = view.optimize_queryset(Book.objects.all())
        # BookSerializer: admin/admin2/file 为 FK，managers/managers2 为 M2M
        assert "managers" in qs._prefetch_related_lookups

    def test_auto_infer_disabled_by_action(self, view):
        view.action = "create"
        qs = view.optimize_queryset(Book.objects.all())
        assert not qs._prefetch_related_lookups
        assert not qs.query.select_related

    def test_auto_infer_disabled_by_flag(self, view):
        view.auto_prefetch_related = False
        qs = view.optimize_queryset(Book.objects.all())
        assert not qs._prefetch_related_lookups
        assert not qs.query.select_related

    def test_explicit_fields_apply_to_all_actions(self, view):
        view.action = "create"
        view.select_related_fields = ("admin2",)
        qs = view.optimize_queryset(Book.objects.all())
        assert "admin2" in qs.query.select_related

    def test_non_queryset_passthrough(self, view):
        plain = [1, 2, 3]
        assert view.optimize_queryset(plain) is plain


class TestCacheMixins:
    def test_detail_cache_key_and_invalid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cache_response, "invalid_cache", lambda key: calls.append(key))

        class BookDetailView(CacheDetailResponseMixin):
            pass

        BookDetailView.invalid_cache("9")
        assert calls == ["BookDetailView_retrieve_9", "BookDetailView_get_9"]

    def test_list_cache_key_and_invalid(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cache_response, "invalid_cache", lambda key: calls.append(key))

        class BookListView(CacheListResponseMixin):
            def list(self, request, *args, **kwargs):
                return []

        BookListView.invalid_cache("7", methods=[])
        assert calls == []

        mixin = BookListView()
        request_1 = Request(RequestFactory().get("/api/demo/book?page=1"))
        request_1.user = type("U", (), {"pk": 7})()
        request_2 = Request(RequestFactory().get("/api/demo/book?page=2"))
        request_2.user = type("U", (), {"pk": 7})()
        key_1 = mixin.get_cache_key(mixin, BookListView.list, request_1, (), {})
        key_2 = mixin.get_cache_key(mixin, BookListView.list, request_2, (), {})
        assert key_1.startswith("BookListView_list_7_")
        assert key_1 != key_2


class TestRunViewByCeleryTask:
    def test_task_false_returns_none(self, view, rf):
        request = Request(rf.get("/api/demo/book/import-data", {"task": "false"}))
        assert run_view_by_celery_task(view, request, {}, [{"name": "x"}]) is None
        assert run_view_by_celery_task(view, request, {}, [{"name": "x"}], batch_length=10) is None


class TestImportExportComposition:
    def test_import_export_actions_registered(self):
        """BookViewSet(BaseModelSet, ImportExportDataAction) 组合下 import/export action 可用。"""
        assert callable(getattr(BookViewSet, "import_data", None))
        assert callable(getattr(BookViewSet, "export_data", None))
        assert "import-data" in BookViewSet.import_data.url_path
        assert "export-data" in BookViewSet.export_data.url_path
