# -*- coding: utf-8 -*-
"""WebSocket consumer 单测（任务日志推送 + 监控面板推送）。

覆盖目标：把 `system/ws.py`(66%) 与 `system/ws_monitor.py`(49%) 的权限判定与
推送分支纳入回归——两处都是**敏感数据出口**（任务日志含导出参数、监控面板含服务凭据），
权限判定出错即信息泄漏，必须有测试锁住。

异步方法用 `asyncio.run` 在同步用例里驱动（不引入 pytest-asyncio：
channels 的 `database_sync_to_async` 本就设计为在事件循环里跑同步 ORM）。
"""

import asyncio
import os

import pytest

from common.celery.utils import CELERY_LOG_MAGIC_MARK
from system.models.export import ExportRecord
from system.models.task import TaskExecution

# database_sync_to_async 会把 ORM 查询丢到独立线程：默认事务包裹下 sqlite 会
# "database table is locked"（另一条连接看不到未提交事务）。故本模块走真实事务。
pytestmark = pytest.mark.django_db(transaction=True)

MONITOR_PERMISSION_PATH = "api/system/monitor/overview$"


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- 任务日志


class TestCanReadTaskLog:
    def test_superuser_allowed(self, superuser):
        from system.ws import can_read_task_log

        record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
        assert can_read_task_log(superuser, record.pk) is True

    def test_owner_allowed(self, normal_user):
        """本人提交的执行记录可读（ExportRecord / TaskExecution 共用 task_id 命名空间）。"""
        from system.ws import can_read_task_log

        record = ExportRecord.objects.create(name="x", file_format="csv", creator=normal_user)
        assert can_read_task_log(normal_user, record.pk) is True

    def test_other_user_denied(self, superuser, normal_user):
        from system.ws import can_read_task_log

        record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
        assert can_read_task_log(normal_user, record.pk) is False

    def test_task_execution_owner_allowed(self, normal_user):
        from system.ws import can_read_task_log

        execution = TaskExecution.objects.create(name="job", creator=normal_user)
        assert can_read_task_log(normal_user, execution.pk) is True

    def test_unknown_pk_denied(self, normal_user):
        """未知 pk 一律拒绝（fail-closed，不因查询落空而放行）。"""
        from system.ws import can_read_task_log

        assert can_read_task_log(normal_user, "00000000-0000-0000-0000-000000000000") is False

    def test_anonymous_denied(self, django_user_model):
        from django.contrib.auth.models import AnonymousUser

        from system.ws import can_read_task_log

        assert can_read_task_log(AnonymousUser(), "x") is False
        assert can_read_task_log(None, "x") is False


class FakeTaskLogConsumer:
    """驱动 `TaskLogNotify` 的推送逻辑：只替换通道 IO（send/close/accept）。"""

    def __init__(self, pk, user, consumer_cls):
        self.pk = pk
        self.user = user
        self.offset = 0
        self.disconnected = False
        self.sent = []
        self.closed = None
        self._cls = consumer_cls

    # --- 通道 IO 替身 ---
    async def send_base_json(self, action, data):
        self.sent.append((action, data))

    async def close(self, code=None):
        self.closed = code

    async def accept(self):
        self.accepted = True

    # --- 复用真实实现 ---
    push_once = property(lambda self: self._cls.push_once.__get__(self))
    push_log_loop = property(lambda self: self._cls.push_log_loop.__get__(self))


def make_consumer(pk, user):
    from system.ws import TaskLogNotify

    return FakeTaskLogConsumer(pk, user, TaskLogNotify)


class TestTaskLogPush:
    def test_push_once_missing_file_uses_execution_state(self, superuser, monkeypatch, tmp_path):
        """文件未落盘：按执行终态判定 finished（与 HTTP log action 同语义）。"""
        from system.ws import TaskLogNotify

        monkeypatch.setattr("system.ws.get_celery_task_log_path", lambda pk: str(tmp_path / "missing.log"))
        execution = TaskExecution.objects.create(name="job", creator=superuser)
        consumer = make_consumer(execution.pk, superuser)
        assert run(TaskLogNotify.push_once(consumer, str(tmp_path / "missing.log"))) is False
        # 未结束：推送空内容且 finished=False
        assert consumer.sent[-1][1]["content"] == ""
        assert consumer.sent[-1][1]["finished"] is False

        from django.utils import timezone

        execution.date_finished = timezone.now()
        execution.save(update_fields=["date_finished"])
        consumer.sent.clear()
        assert run(TaskLogNotify.push_once(consumer, str(tmp_path / "missing.log"))) is True
        assert consumer.sent[-1][1]["finished"] is True

    def test_push_once_reads_content_and_detects_mark(self, superuser, monkeypatch, tmp_path):
        """读到内容 + 结尾标记：一次性推送内容并结束循环。"""
        from system.ws import TaskLogNotify

        path = tmp_path / "job.log"
        path.write_bytes(b"hello world" + CELERY_LOG_MAGIC_MARK)
        execution = TaskExecution.objects.create(name="job", creator=superuser)
        consumer = make_consumer(execution.pk, superuser)
        assert run(TaskLogNotify.push_once(consumer, str(path))) is True
        action, payload = consumer.sent[-1]
        assert payload["content"] == "hello world"  # 标记被剥离
        assert payload["finished"] is True
        assert payload["offset"] == os.path.getsize(path)

    def test_push_once_without_mark_keeps_waiting(self, superuser, tmp_path):
        """有内容但无结束标记：不结束（继续增量推送）。"""
        from system.ws import TaskLogNotify

        path = tmp_path / "job.log"
        path.write_bytes(b"partial output")
        execution = TaskExecution.objects.create(name="job", creator=superuser)
        consumer = make_consumer(execution.pk, superuser)
        assert run(TaskLogNotify.push_once(consumer, str(path))) is False
        assert consumer.sent[-1][1]["content"] == "partial output"

    def test_tail_has_mark_short_file(self, tmp_path):
        from system.ws import _tail_has_mark

        path = tmp_path / "tiny.log"
        path.write_bytes(b"ab")
        assert run(_tail_has_mark(str(path))) is False

    def test_connect_rejects_anonymous_and_unauthorized(self, superuser, normal_user):
        """未登录 4401 / 无权限 4403（敏感日志出口，必须 fail-closed）。"""
        from system.ws import TaskLogNotify

        record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)

        anon = make_consumer(record.pk, None)
        anon.scope = {"user": None, "url_route": {"kwargs": {"pk": record.pk}}}
        run(TaskLogNotify.connect(anon))
        assert anon.closed == 4401

        other = make_consumer(record.pk, normal_user)
        other.scope = {"user": normal_user, "url_route": {"kwargs": {"pk": record.pk}}}
        run(TaskLogNotify.connect(other))
        assert other.closed == 4403

    def test_connect_accepts_owner(self, normal_user):
        from system.ws import TaskLogNotify

        record = ExportRecord.objects.create(name="x", file_format="csv", creator=normal_user)
        consumer = make_consumer(record.pk, normal_user)
        consumer.scope = {"user": normal_user, "url_route": {"kwargs": {"pk": record.pk}}}
        run(TaskLogNotify.connect(consumer))
        assert consumer.closed is None
        assert getattr(consumer, "accepted", False) is True

    def test_ping_is_noop(self):
        """任务日志连接不进消息分组，心跳必须静默（否则基类抛 AttributeError 断连）。"""
        from system.ws import TaskLogNotify

        consumer = make_consumer("x", None)
        assert run(TaskLogNotify.ping(consumer, {})) is None

    def test_disconnect_stops_loop(self):
        from system.ws import TaskLogNotify

        consumer = make_consumer("x", None)
        consumer.scope = {"user": None, "url_route": {"kwargs": {"pk": "x"}}}
        run(TaskLogNotify.disconnect(consumer, 1000))
        assert consumer.disconnected is True


# --------------------------------------------------------------------------- 监控面板


class TestMonitorPermission:
    def test_superuser_allowed(self, superuser):
        from system.ws_monitor import _has_monitor_permission

        assert run(_has_monitor_permission(superuser)) is True

    def test_menu_granted_allowed(self, normal_user, role, menu_factory):
        from system.ws_monitor import _has_monitor_permission

        menu = menu_factory("monitor-overview", path=MONITOR_PERMISSION_PATH, method="GET")
        role.menu.add(menu)
        assert run(_has_monitor_permission(normal_user)) is True

    def test_without_menu_denied(self, normal_user):
        from system.ws_monitor import _has_monitor_permission

        assert run(_has_monitor_permission(normal_user)) is False

    def test_anonymous_denied(self):
        from django.contrib.auth.models import AnonymousUser

        from system.ws_monitor import _has_monitor_permission

        assert run(_has_monitor_permission(AnonymousUser())) is False
        assert run(_has_monitor_permission(None)) is False

    def test_permission_lookup_failure_fail_closed(self, normal_user, monkeypatch):
        """权限读取异常：fail-closed（不因异常放行监控数据）。"""
        from system.ws_monitor import _has_monitor_permission

        def boom(*args, **kwargs):
            raise RuntimeError("permission backend down")

        monkeypatch.setattr("common.core.permission.get_user_permission", boom)
        assert run(_has_monitor_permission(normal_user)) is False

    def test_collect_panel_sections(self, superuser):
        """panel 采集与 HTTP 监控接口共用函数（口径一致性）。"""
        from system.ws_monitor import _collect_panel

        payload = run(_collect_panel())
        assert payload["section"] == "panel"
        for key in ("services", "redis", "celery", "slow"):
            assert key in payload


class TestMonitorNotify:
    """监控面板 consumer：鉴权分支与推送循环（用替身驱动通道 IO）。"""

    @staticmethod
    def _consumer(user):
        from system.ws_monitor import MonitorNotify

        class Fake:
            disconnected = False

            def __init__(self):
                self.sent = []
                self.closed = None
                self.scope = {"user": user}

            async def send_base_json(self, action, data):
                self.sent.append((action, data))

            async def close(self, code=None):
                self.closed = code

            async def accept(self):
                self.accepted = True

            connect = MonitorNotify.connect
            disconnect = MonitorNotify.disconnect
            ping = MonitorNotify.ping
            push_loop = MonitorNotify.push_loop

        return Fake()

    def test_anonymous_rejected(self):
        from system.ws_monitor import MonitorNotify

        consumer = self._consumer(None)
        run(MonitorNotify.connect(consumer))
        assert consumer.closed == 4401

    def test_unauthorized_rejected(self, normal_user):
        """无 SystemMonitor 菜单权限：4403（监控数据属敏感信息）。"""
        from system.ws_monitor import MonitorNotify

        consumer = self._consumer(normal_user)
        run(MonitorNotify.connect(consumer))
        assert consumer.closed == 4403

    def test_authorized_accepts(self, superuser, monkeypatch):
        from system.ws_monitor import MonitorNotify

        monkeypatch.setattr("system.ws_monitor.LIVE_PUSH_INTERVAL", 0)
        consumer = self._consumer(superuser)
        run(MonitorNotify.connect(consumer))
        assert consumer.closed is None
        assert getattr(consumer, "accepted", False) is True

    def test_push_loop_sends_live_then_panel(self, superuser, monkeypatch):
        """首 tick 同时推 live 与 panel（连接建立即有一帧兜底）。"""
        monkeypatch.setattr("system.ws_monitor.LIVE_PUSH_INTERVAL", 0)
        consumer = self._consumer(superuser)
        consumer.disconnected = False

        async def stop_after_two_frames():
            # 让循环跑满 2 tick 后标记断开，验证 live 每 tick 推、panel 每 6 tick 推
            task = asyncio.create_task(consumer.push_loop())
            while len(consumer.sent) < 2:
                await asyncio.sleep(0)
            consumer.disconnected = True
            await asyncio.sleep(0)
            task.cancel()
            return consumer.sent

        frames = run(stop_after_two_frames())
        sections = [frame[1].get("section") for frame in frames]
        assert "live" in sections
        assert "panel" in sections

    def test_ping_is_noop(self):
        from system.ws_monitor import MonitorNotify

        consumer = self._consumer(None)
        assert run(MonitorNotify.ping(consumer, {})) is None
