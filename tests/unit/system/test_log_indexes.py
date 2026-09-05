# -*- coding: utf-8 -*-
"""PERF-04 索引迁移测试：确认高增长表的过滤/排序字段已建立索引。"""
import pytest
from django.db import connection

from notifications.models import MessageContent, MessageUserRead
from system.models import OperationLog, UploadFile, UserLoginLog

pytestmark = pytest.mark.django_db

EXPECTED = {
    OperationLog: {'idx_oplog_created', 'idx_oplog_module_created', 'idx_oplog_request_uuid'},
    UserLoginLog: {'idx_loginlog_created'},
    UploadFile: {'idx_uploadfile_tmp_created', 'idx_uploadfile_md5sum'},
    MessageContent: {'idx_msg_created'},
}


@pytest.mark.parametrize("model", list(EXPECTED))
def test_indexes_exist(model):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
    indexes = {name for name, item in constraints.items() if item.get("index")}
    assert EXPECTED[model] <= indexes


def test_message_user_read_single_column_unread_index_removed():
    """单列 unread 索引与 (owner, unread) 复合索引左前缀重复，应已移除"""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, MessageUserRead._meta.db_table)
    indexes = {name: item for name, item in constraints.items() if item.get("index")}
    single_unread = [name for name, item in indexes.items()
                     if item.get("columns") == ["unread"] or item.get("columns") == ["unread"]]
    assert not single_unread
    # unique_together (owner, notice) 之外，还应保留 (owner, unread) 复合索引
    assert any(list(item.get("columns", []))[-2:] == ["owner_id", "unread"] or
               item.get("columns") == ["owner", "unread"] for item in indexes.values())
