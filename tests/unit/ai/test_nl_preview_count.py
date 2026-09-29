# -*- coding: utf-8 -*-
"""NL 预览计数（bounded_preview_count）：LIMIT+1 探测替代全量 COUNT。

口径：不超过上限时返回真实行数（capped=False）；超过上限时返回上限值并标记 capped=True
（调用方文案标注「≥ N」）——避免大表 + 复杂过滤下全量 COUNT 把 SSE 尾帧拖住。
"""

import pytest

from ai.utils.nl_query import PREVIEW_PROBE_LIMIT, bounded_preview_count
from system.models import UserInfo

pytestmark = pytest.mark.django_db


def _make_users(count):
    return [UserInfo.objects.create_user(username=f"nlprobe_{index}", password="Test@123456") for index in range(count)]


class TestBoundedPreviewCount:
    def test_counts_exactly_when_below_limit(self):
        _make_users(3)
        queryset = UserInfo.objects.filter(username__startswith="nlprobe_")
        count, capped = bounded_preview_count(queryset, UserInfo, limit=10)
        assert (count, capped) == (3, False)

    def test_counts_exactly_at_limit(self):
        """恰好等于上限不算截断（探测多取一行就是为了区分 = 与 >）。"""
        _make_users(5)
        queryset = UserInfo.objects.filter(username__startswith="nlprobe_")
        count, capped = bounded_preview_count(queryset, UserInfo, limit=5)
        assert (count, capped) == (5, False)

    def test_capped_when_above_limit(self):
        _make_users(6)
        queryset = UserInfo.objects.filter(username__startswith="nlprobe_")
        count, capped = bounded_preview_count(queryset, UserInfo, limit=5)
        assert (count, capped) == (5, True)

    def test_empty_queryset(self):
        queryset = UserInfo.objects.filter(username__startswith="nlprobe_none_")
        assert bounded_preview_count(queryset, UserInfo, limit=5) == (0, False)

    def test_probe_only_fetches_limit_plus_one_rows(self):
        """探测 SQL 必须带 LIMIT：用 sqlite 的查询计数与 SQL 形态作证。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        _make_users(20)
        queryset = UserInfo.objects.filter(username__startswith="nlprobe_")
        with CaptureQueriesContext(connection) as captured:
            bounded_preview_count(queryset, UserInfo, limit=3)
        assert len(captured) == 1
        sql = captured[0]["sql"].lower()
        assert "limit" in sql
        # 不得出现 COUNT(*)（全量计数被 PET 化掉的正是它）
        assert "count(" not in sql

    def test_default_limit_matches_row_cap(self):
        """默认探测上限与 NL 行数上限同源（NL_ROW_LIMIT_CAP）。"""
        from ai.utils.nl_query import NL_ROW_LIMIT_CAP

        assert PREVIEW_PROBE_LIMIT == NL_ROW_LIMIT_CAP


class TestPreviewText:
    def test_capped_text_marks_lower_bound(self):
        from ai.views.nl_query import _preview_text

        assert _preview_text(200, True) == "≥200"
        assert _preview_text(7, False) == "7"
