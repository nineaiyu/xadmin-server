# -*- coding: utf-8 -*-
"""可手动执行任务白名单（T02-04）：写入侧 + 执行侧 + registered 标记。

celery 注册表里的任意 ``@shared_task``（含删数据/改密等高危任务）原先都可经
任务管理页配成周期任务或「立即运行」。白名单机制（SysConfig
``MANUAL_RUNNABLE_TASKS``，fnmatch 通配符）：

- 写入侧：PeriodicTaskSerializer.task 白名单校验，默认拒绝未登记任务；
- 执行侧：run / batch-run 的 ``_dispatch_periodic_run`` 同口径再拦一道
  （执行侧用例见 test_task_execution.py，只挡写入不挡执行会让存量任务绕过）；
- 下发侧：registered 动作附 ``runnable`` 标记（前端按标记过滤下拉）。
"""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.serializers.task import PeriodicTaskSerializer
from system.utils.task_whitelist import is_task_runnable, manual_runnable_tasks
from system.views.task_periodic import PeriodicTaskViewSet

pytestmark = pytest.mark.django_db

DEMO_TASK = "demo.tasks.auto_off_shelf_books"
DANGEROUS_TASK = "system.tasks.auto_clean_operation_job"


def _set_whitelist(monkeypatch, tasks):
    """覆写白名单读取口（SysConfig 属性），与 test_task_execution 同手法。

    tasks 允许 list / str（含逗号与换行分隔）——注意不能对 str 做 list()，
    否则会按字符拆散。
    """
    from common.core.config import SysConfig

    monkeypatch.setattr(type(SysConfig), "MANUAL_RUNNABLE_TASKS", property(lambda self: tasks), raising=False)


class TestWhitelistParsing:
    def test_list_value_passthrough_and_dedupe(self, monkeypatch):
        _set_whitelist(monkeypatch, ["a.tasks.x", "a.tasks.x", "b.tasks.y"])
        assert manual_runnable_tasks() == ("a.tasks.x", "b.tasks.y")

    def test_string_value_split_on_comma_and_newline(self, monkeypatch):
        _set_whitelist(monkeypatch, "a.tasks.x, b.tasks.y\nb.tasks.y ,")
        assert manual_runnable_tasks() == ("a.tasks.x", "b.tasks.y")

    def test_is_task_runnable_exact_and_wildcard(self, monkeypatch):
        _set_whitelist(monkeypatch, [DEMO_TASK, "system.tasks.health_*"])
        assert is_task_runnable(DEMO_TASK) is True
        assert is_task_runnable("system.tasks.health_check") is True
        assert is_task_runnable(DANGEROUS_TASK) is False

    def test_empty_whitelist_denies_everything(self, monkeypatch):
        """空清单 = 默认拒绝全部（fail-closed：业务方确认范围前的安全阀）。"""
        _set_whitelist(monkeypatch, [])
        assert manual_runnable_tasks() == ()
        assert is_task_runnable(DEMO_TASK) is False
        assert is_task_runnable(None) is False


class TestPeriodicTaskWriteSide:
    def test_serializer_rejects_task_outside_whitelist(self, monkeypatch):
        _set_whitelist(monkeypatch, [DEMO_TASK])
        serializer = PeriodicTaskSerializer(data={"name": "越权任务", "task": DANGEROUS_TASK})
        assert not serializer.is_valid()
        assert "task" in serializer.errors
        assert "whitelist" in str(serializer.errors["task"])

    def test_serializer_allows_whitelisted_task(self, monkeypatch):
        _set_whitelist(monkeypatch, [DEMO_TASK])
        serializer = PeriodicTaskSerializer(data={"name": "演示任务", "task": DEMO_TASK})
        serializer.is_valid()
        # 其他字段（如必填调度关联）可能仍有校验错误，但 task 字段必须通过
        assert "task" not in serializer.errors, serializer.errors


class TestRegisteredFlag:
    def test_registered_includes_runnable_flag(self, superuser, monkeypatch):
        """registered 动作附 runnable 标记：白名单外任务 fail-closed 标记为不可执行。"""
        _set_whitelist(monkeypatch, [DEMO_TASK])
        factory = APIRequestFactory()
        request = factory.get("/api/system/periodic-task/registered")
        force_authenticate(request, user=superuser)
        response = PeriodicTaskViewSet.as_view({"get": "registered"})(request)
        items = response.data["data"]
        assert items, "autodiscover 后应至少注册一个任务"
        assert all("runnable" in item for item in items)
        by_name = {item["name"]: item for item in items}
        assert by_name[DEMO_TASK]["runnable"] is True
        if DANGEROUS_TASK in by_name:
            assert by_name[DANGEROUS_TASK]["runnable"] is False
