# -*- coding: utf-8 -*-
"""common/signal_handlers.py 回归：celery 启停钩子、任务日志清理、creator/modifier 自动落值。

creator/modifier 直接以 kwargs 调用 receiver（不经 save），请求上下文通过 patch
`get_current_request` 注入——threadlocal 在单测里本就没有请求。
"""

import logging
from types import SimpleNamespace

from django.core.cache import cache
from django_celery_results.models import TaskResult

import common.signal_handlers as sh
from common.celery.decorator import get_after_app_ready_tasks
from common.signals import django_ready

# 触发 registry 注册（auto_clean_monitor_logs 等四个 after_app_ready 任务）
import common.tasks  # noqa: F401


def _stub_request(user):
    return SimpleNamespace(user=user, method="GET", get_full_path=lambda: "/x/")


# --------------------------------------------------------------------------- celery 启停


class TestOnAppReady:
    def test_flag_short_circuit(self, monkeypatch):
        cache.set("CELERY_APP_READY", 1, 10)
        called = []
        monkeypatch.setattr(sh, "signature", lambda task: SimpleNamespace(delay=lambda: called.append(task)))
        sh.on_app_ready()
        assert called == []

    def test_enabled_task_dispatched(self, monkeypatch, db):
        cache.delete("CELERY_APP_READY")
        called = []
        monkeypatch.setattr(sh, "signature", lambda task: SimpleNamespace(delay=lambda: called.append(task)))
        sh.on_app_ready()
        # 注册表里的任务没有 PeriodicTask 记录时直接执行一次
        assert sorted(called) == sorted(get_after_app_ready_tasks())

    def test_disabled_periodic_task_skipped(self, monkeypatch, db):
        cache.delete("CELERY_APP_READY")
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        called = []
        monkeypatch.setattr(sh, "signature", lambda task: SimpleNamespace(delay=lambda: called.append(task)))
        interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.create(
            name="t", task="common.tasks.auto_clean_monitor_logs", enabled=False, interval=interval
        )
        sh.on_app_ready()
        assert "common.tasks.auto_clean_monitor_logs" not in called


class TestAfterAppShutdown:
    def test_clean_registered_periodic_tasks(self, monkeypatch, db):
        cache.delete("CELERY_APP_SHUTDOWN")
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        monkeypatch.setattr(sh, "get_after_app_shutdown_clean_tasks", lambda: ["ghost_task"])
        interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.create(name="ghost_task", task="no.such.task", interval=interval)
        sh.after_app_shutdown_periodic_tasks()
        assert not PeriodicTask.objects.filter(name="ghost_task").exists()

    def test_flag_short_circuit(self, monkeypatch, db):
        cache.set("CELERY_APP_SHUTDOWN", 1, 10)
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        monkeypatch.setattr(sh, "get_after_app_shutdown_clean_tasks", lambda: ["ghost_task"])
        interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.SECONDS)
        PeriodicTask.objects.create(name="ghost_task", task="no.such.task", interval=interval)
        sh.after_app_shutdown_periodic_tasks()
        assert PeriodicTask.objects.filter(name="ghost_task").exists()


# --------------------------------------------------------------------------- 日志与清理


class TestDeleteFileHandler:
    def test_removes_task_log(self, monkeypatch):
        removed = []
        monkeypatch.setattr(sh, "remove_file", lambda path: removed.append(path))
        instance = TaskResult(task_id="abc-123")
        sh.delete_file_handler(sender=None, instance=instance)
        assert removed and "abc-123" in removed[0]

    def test_noop_without_instance_or_task_id(self, monkeypatch):
        removed = []
        monkeypatch.setattr(sh, "remove_file", lambda path: removed.append(path))
        sh.delete_file_handler(sender=None, instance=None)
        sh.delete_file_handler(sender=None, instance=TaskResult(task_id=""))
        assert removed == []


class TestAfterSetupLogger:
    def test_adds_celery_file_handler(self):
        log = logging.Logger("t_after_setup", level=logging.INFO)
        sh.on_after_setup_logger(logger=log, loglevel=logging.INFO, format="%(message)s")
        assert any(isinstance(h, sh.CeleryThreadTaskFileHandler) for h in log.handlers)

    def test_noop_without_logger(self):
        sh.on_after_setup_logger(logger=None, loglevel=logging.INFO, format="%(message)s")


# --------------------------------------------------------------------------- creator/modifier


class TestAutoCreatorModifier:
    def test_creator_set_from_request(self, monkeypatch, superuser):
        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(superuser))
        instance = SimpleNamespace(creator=None, _ignore_auto_creator=False)
        sh.on_create_set_creator(sender=None, instance=instance)
        assert instance.creator == superuser

    def test_dept_belong_follows_creator(self, monkeypatch, superuser):
        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(superuser))
        instance = SimpleNamespace(creator=None, _ignore_auto_creator=False, dept_belong=None, dept="dept-pk")
        sh.on_create_set_creator(sender=None, instance=instance)
        assert instance.creator == superuser
        assert instance.dept_belong == superuser.dept

    def test_ignore_flag_and_existing_creator_untouched(self, monkeypatch, superuser):
        keeper = object()
        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(superuser))
        ignored = SimpleNamespace(creator=None, _ignore_auto_creator=True)
        sh.on_create_set_creator(sender=None, instance=ignored)
        assert ignored.creator is None

        owned = SimpleNamespace(creator=keeper, _ignore_auto_creator=False)
        sh.on_create_set_creator(sender=None, instance=owned)
        assert owned.creator is keeper

    def test_no_request_or_anonymous_untouched(self, monkeypatch):
        from django.contrib.auth.models import AnonymousUser

        monkeypatch.setattr(sh, "get_current_request", lambda: None)
        instance = SimpleNamespace(creator=None, _ignore_auto_creator=False)
        sh.on_create_set_creator(sender=None, instance=instance)
        assert instance.creator is None

        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(AnonymousUser()))
        instance2 = SimpleNamespace(creator=None, _ignore_auto_creator=False)
        sh.on_create_set_creator(sender=None, instance=instance2)
        assert instance2.creator is None

    def test_modifier_set_from_request(self, monkeypatch, superuser):
        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(superuser))
        instance = SimpleNamespace(modifier=None, _ignore_auto_modifier=False)
        sh.on_update_set_modifier(sender=None, instance=instance)
        assert instance.modifier == superuser

    def test_modifier_ignored_flag_and_no_attr(self, monkeypatch, superuser):
        monkeypatch.setattr(sh, "get_current_request", lambda: _stub_request(superuser))
        ignored = SimpleNamespace(modifier=None, _ignore_auto_modifier=True)
        sh.on_update_set_modifier(sender=None, instance=ignored)
        assert ignored.modifier is None
        plain = SimpleNamespace()  # 无 modifier 字段的模型直接跳过
        sh.on_update_set_modifier(sender=None, instance=plain)


# --------------------------------------------------------------------------- DEBUG_DEV 查询日志


class TestRequestFinishedQueryLog:
    def test_counts_and_prints_summary(self, monkeypatch, capsys):
        from django.db import connection

        connection.queries_log.clear()
        connection.queries_log.append({"sql": "SELECT * FROM `system_userinfo` WHERE x = 1", "time": "0.02"})
        connection.queries_log.append({"sql": "SELECT * FROM `system_userinfo` WHERE y = 2", "time": "0.03"})
        connection.queries_log.append({"sql": "INSERT INTO `x` VALUES (1)", "time": "0.01"})
        monkeypatch.setattr(sh, "get_current_request", lambda: None)

        sh.on_request_finished_logging_db_query(sender=None)
        out = capsys.readouterr().out
        assert ">>>. [GET] /Unknown" in out

    def test_no_queries_returns_early(self, monkeypatch, capsys):
        from django.db import connection

        connection.queries_log.clear()
        sh.on_request_finished_logging_db_query(sender=None)
        assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- django_ready


class TestClearResponseCache:
    def test_clears_magic_response_cache(self, db):
        cache.set("magic_cache_response_x", 1, 10)
        django_ready.send(object())
        assert cache.get("magic_cache_response_x") is None
