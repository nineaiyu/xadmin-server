# -*- coding: utf-8 -*-
"""公告量放大后的「全部已读」写入（真实量级证据）。

历史实现是逐条补建已读行（2N 条 SQL）；现实现把 pk 过滤与「尚无已读行」差集全部
下推到 DB，写入按 1000 条一批 bulk_create。本用例用 20 条与 1200 条两档对比
**SQL 条数不随量级增长**，并核对已读行确实落库。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from notifications.models import MessageContent, MessageUserRead

pytestmark = pytest.mark.django_db

ALL_READ_URL = "/api/notifications/site-messages/all-read"


def _publish(count: int, prefix: str) -> None:
    MessageContent.objects.bulk_create(
        [
            MessageContent(
                title=f"{prefix}-{index}",
                message="公告正文",
                notice_type=MessageContent.NoticeChoices.NOTICE,
                publish=True,
            )
            for index in range(count)
        ]
    )


def _all_read_queries(auth_client) -> int:
    with CaptureQueriesContext(connection) as captured:
        resp = auth_client.patch(ALL_READ_URL, {}, format="json")
    assert resp.status_code == 200, resp.data
    return len(captured)


class TestAllReadVolume:
    def test_query_count_flat_when_backlog_grows(self, auth_client, superuser):
        _publish(20, "small")
        baseline = _all_read_queries(auth_client)
        created_small = MessageUserRead.objects.filter(owner=superuser, unread=False).count()

        # 再灌 1200 条公告：量级 ×60，SQL 条数不应跟着长
        _publish(1200, "large")
        grown = _all_read_queries(auth_client)
        created_total = MessageUserRead.objects.filter(owner=superuser, unread=False).count()

        assert created_small >= 20
        assert created_total >= 1220
        # 允许 ±2 的常数抖动（批量边界 / 计数查询），不允许随行数线性增长
        assert grown <= baseline + 2, f"all-read SQL 随公告量增长：{baseline} → {grown}"

    def test_rows_land_then_second_call_is_noop(self, auth_client, superuser):
        _publish(1100, "repeat")
        _all_read_queries(auth_client)
        first = MessageUserRead.objects.filter(owner=superuser).count()
        _all_read_queries(auth_client)
        assert MessageUserRead.objects.filter(owner=superuser).count() == first
