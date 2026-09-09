# -*- coding:utf-8 -*-
"""行级变更历史：object_pk 精确过滤 + 索引守护。

变更历史 = 操作日志按对象回溯：中间件从 detail 路由 URL kwargs 提取
object_pk（pk 兜底 id，转 str 兼容 UUID/整型），前端按
object_pk=<pk> + path 前缀（缩小到本资源）查询；
复用 OperationLog 既有存储/留存/脱敏链路，不新增模型。
"""

import pytest
from django.db import connection

from system.models import OperationLog
from system.views.admin.operationlog import OperationLogFilter

pytestmark = pytest.mark.django_db


def _log(path, **kwargs):
    defaults = {"method": "PATCH", "status_code": 1000, "response_code": 200}
    defaults.update(kwargs)
    return OperationLog.objects.create(path=path, **defaults)


def test_object_pk_filter_scopes_to_object():
    """object_pk 只命中该行的日志：跨模块同值主键经 path 前缀过滤排除串扰。"""
    target = _log("/api/system/user/7", object_pk="7", module="用户管理", changes='{"nickname": ["a", "b"]}')
    _log("/api/system/user/77", object_pk="77", module="用户管理")
    _log("/api/system/user", module="用户管理", method="POST")
    _log("/api/system/role/7", object_pk="7", module="角色管理")

    queryset = OperationLogFilter(
        data={"object_pk": "7", "path": "/api/system/user/"}, queryset=OperationLog.objects.all(), request=None
    ).qs
    assert queryset.count() == 1
    assert queryset.get().pk == target.pk


def test_path_exact_filter_scopes_to_object():
    """path_exact（保留的兼容过滤器）只命中该行 detail 路由日志。"""
    target = _log("/api/system/user/7", module="用户管理", changes='{"nickname": ["a", "b"]}')
    _log("/api/system/user/77", module="用户管理")
    _log("/api/system/user", module="用户管理", method="POST")
    _log("/api/system/role/7", module="角色管理")

    queryset = OperationLogFilter(
        data={"path_exact": "/api/system/user/7"}, queryset=OperationLog.objects.all(), request=None
    ).qs
    assert queryset.count() == 1
    assert queryset.get().pk == target.pk


def test_module_objectpk_index_exists():
    """守护：module + object_pk 复合索引存在（test_log_indexes EXPECTED 同步防漂移）。"""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, OperationLog._meta.db_table)
    indexes = {name for name, item in constraints.items() if item.get("index")}
    assert "idx_oplog_module_objectpk" in indexes


def test_path_index_exists():
    """守护：path 索引存在（变更历史 path 前缀过滤使用）。"""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, OperationLog._meta.db_table)
    indexes = {name for name, item in constraints.items() if item.get("index")}
    assert "idx_oplog_path" in indexes


def test_changes_rows_have_diff_payload():
    """变更历史弹窗的数据口径：update 行带 changes，可解析出 old/new。"""
    import json

    changes = {"nickname": {"old": "旧昵称", "new": "新昵称"}}
    record = _log("/api/system/user/9", changes=json.dumps(changes))
    log = OperationLog.objects.get(pk=record.pk)
    parsed = json.loads(log.changes)
    assert parsed["nickname"]["new"] == "新昵称"
