# -*- coding: utf-8 -*-
"""导出/导入重 IO 专用限流（ExportImportThrottle）守护测试。

覆盖：档位登记、mixin 按 MRO 并集命中声明 action、非声明 action 不挂档、
真实请求打满后 429（业务码路径在集成测试另有覆盖）。限流计数经 autouse
_clean_cache 每用例清空，单用例内连续请求可稳定打满固定窗口。
"""

import pytest
from django.conf import settings

from common.core.throttle import ExportImportThrottle, ExportImportThrottleMixin

pytestmark = pytest.mark.django_db

BOOK_LIST_URL = "/api/demo/book"


def _declared_actions(viewset_cls) -> frozenset:
    actions: set = set()
    for klass in viewset_cls.__mro__:
        actions.update(getattr(klass, "export_import_actions", ()) or ())
    return frozenset(actions)


class TestExportImportThrottle:
    def test_rate_registered(self):
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["export_import"]
        assert rate == "30/m"

    def test_mixin_appends_for_declared_action(self):
        class _Base:
            def get_throttles(self):
                return ["base"]

        class _View(ExportImportThrottleMixin, _Base):
            export_import_actions = ("export_data",)

        view = _View()
        view.action = "export_data"
        throttles = view.get_throttles()
        assert throttles[0] == "base"
        assert isinstance(throttles[1], ExportImportThrottle)

        view.action = "list"
        assert view.get_throttles() == ["base"]

    def test_declarations_union_across_mixins(self):
        """导出/导入 Action mixin 与下载 mixin 各自声明，组合视图自动合并。"""
        from demo.views import BookViewSet
        from task.views.admin.export import ExportRecordViewSet
        from task.views.admin.import_ import ImportRecordViewSet

        assert _declared_actions(ExportRecordViewSet) == {"download"}
        assert _declared_actions(ImportRecordViewSet) == {"download"}
        assert _declared_actions(BookViewSet) == {
            "export_data",
            "export_async",
            "import_headers",
            "import_validate",
            "import_async",
        }

    def test_429_after_rate_exhausted(self, auth_client, superuser):
        """同用户连续请求打满 export_import 档：第 N+1 次 429。"""
        from demo.models import Book

        Book.objects.create(name="限流书", isbn="i-o8-8", author="a", admin=superuser, admin2=superuser)
        limit = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["export_import"].split("/")[0])
        codes = [auth_client.get(f"{BOOK_LIST_URL}/export-data?type=xlsx").status_code for _ in range(limit + 1)]
        assert all(code == 200 for code in codes[:-1])
        assert codes[-1] == 429
