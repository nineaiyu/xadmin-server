# -*- coding: utf-8 -*-
"""消息中心索引守护（P4）。

背景：未读数 = 「公告类（NOTICE/DEPT/ROLE）未被读过 ∪ 个人类 unread=True」的 OR 查询，
消息量增长后缺索引会让 `notice_type` 判定退化为全表扫描；个人未读依赖
`(owner, unread)` 复合索引（单列 owner 冗余索引已删除）。
"""

import pytest
from django.db import connection

from notifications.models import MessageContent, MessageUserRead

pytestmark = pytest.mark.django_db


def _db_index_columns(table: str) -> set[tuple]:
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {tuple(info["columns"]) for info in constraints.values() if info.get("index")}


def test_message_content_notice_type_index_exists():
    assert ("notice_type",) in _db_index_columns(MessageContent._meta.db_table)


def test_message_user_read_owner_unread_index_exists():
    assert ("owner_id", "unread") in _db_index_columns(MessageUserRead._meta.db_table)


def test_model_meta_declares_indexes():
    """模型声明与迁移保持同步（makemigrations --check 之外的第二道守护）。"""
    content_indexes = {tuple(idx.fields) for idx in MessageContent._meta.indexes}
    assert ("created_time",) in content_indexes
    assert ("notice_type",) in content_indexes
    read_indexes = {tuple(idx.fields) for idx in MessageUserRead._meta.indexes}
    assert read_indexes == {("owner", "unread")}
