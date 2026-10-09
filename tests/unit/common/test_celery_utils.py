# -*- coding: utf-8 -*-
"""packages/xadmin-common/common/celery/utils.py：任务日志路径与定时任务注册。"""

from datetime import timedelta

import pytest

from common.celery.utils import (
    CELERY_LOG_MAGIC_MARK,
    eta_second,
    get_celery_task_log_path,
    get_task_log_path,
    make_dirs,
)


def test_make_dirs_creates_nested(tmp_path):
    target = tmp_path / "a" / "b"
    make_dirs(str(target))
    assert target.is_dir()


def test_get_task_log_path_builds_level_dirs(tmp_path):
    # task_id[:level] 按字符展开为子目录（a/a/...）
    task_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    path = get_task_log_path(str(tmp_path), task_id, level=2)
    assert path == str(tmp_path / "a" / "a" / f"{task_id}.log")
    assert (tmp_path / "a" / "a").is_dir()


def test_get_celery_task_log_path_uses_settings(tmp_path, settings):
    settings.CELERY_LOG_DIR = str(tmp_path / "logs")
    path = get_celery_task_log_path("task-1")
    assert path.startswith(str(tmp_path / "logs"))
    assert path.endswith("task-1.log")


def test_eta_second_future():
    # 两次调用间存在微秒差，用 1s 容差断言偏移量
    diff = eta_second(30) - eta_second(0)
    assert timedelta(seconds=29) < diff <= timedelta(seconds=31)


def test_log_magic_mark():
    assert CELERY_LOG_MAGIC_MARK == b"\x00" * 5


@pytest.mark.django_db
class TestPeriodicTasks:
    def test_interval_task_created_and_updated(self):
        from django_celery_beat.models import PeriodicTask

        from common.celery.utils import create_or_update_celery_periodic_tasks

        spec = {
            "test-interval-task": {
                "task": "common.tasks.test_dummy",
                "interval": 30,
                "args": (1, 2),
                "kwargs": {"k": "v"},
                "description": "单测",
            }
        }
        created_map = create_or_update_celery_periodic_tasks(spec)
        # 返回 {任务名: (obj, created)}，元组来自 update_or_create
        assert created_map is not None
        task, created = created_map["test-interval-task"]
        assert created is True
        assert task.name == "test-interval-task"
        assert task.interval.every == 30

        again = create_or_update_celery_periodic_tasks(spec)
        assert again is not None
        again_task, again_created = again["test-interval-task"]
        assert again_task.name == "test-interval-task"
        assert again_created is False
        assert PeriodicTask.objects.filter(name="test-interval-task").count() == 1

        # 清理，避免影响其他测试
        PeriodicTask.objects.filter(name="test-interval-task").delete()

    def test_crontab_task_created(self):
        from django_celery_beat.models import PeriodicTask

        from common.celery.utils import create_or_update_celery_periodic_tasks

        spec = {
            "test-crontab-task": {
                "task": "common.tasks.test_dummy",
                "crontab": "30 7 * * 1-5",
            }
        }
        created_map = create_or_update_celery_periodic_tasks(spec)
        assert created_map is not None
        task, _ = created_map["test-crontab-task"]
        assert task.crontab.minute == "30"
        PeriodicTask.objects.filter(name="test-crontab-task").delete()

    def test_multi_tasks_all_registered(self):
        """多任务字典必须全部注册（历史缺陷：return 缩进在循环内，只处理第一个）。"""
        from django_celery_beat.models import PeriodicTask

        from common.celery.utils import create_or_update_celery_periodic_tasks

        spec = {
            "test-multi-a": {"task": "common.tasks.test_dummy", "interval": 30},
            "test-multi-b": {"task": "common.tasks.test_dummy", "interval": 60},
        }
        created_map = create_or_update_celery_periodic_tasks(spec)
        assert created_map is not None
        assert set(created_map.keys()) == {"test-multi-a", "test-multi-b"}
        assert PeriodicTask.objects.filter(name__startswith="test-multi-").count() == 2
        PeriodicTask.objects.filter(name__startswith="test-multi-").delete()

    def test_empty_tasks_returns_empty_map(self):
        from common.celery.utils import create_or_update_celery_periodic_tasks

        assert create_or_update_celery_periodic_tasks({}) == {}

    def test_invalid_schedule_returns_none(self):
        from common.celery.utils import create_or_update_celery_periodic_tasks

        assert create_or_update_celery_periodic_tasks({"bad": {"task": "x", "interval": "not-int"}}) is None

    def test_invalid_crontab_returns_none(self):
        from common.celery.utils import create_or_update_celery_periodic_tasks

        assert create_or_update_celery_periodic_tasks({"bad": {"task": "x", "crontab": "not-a-crontab"}}) is None

    def test_disable_and_delete_periodic_task(self):
        from django_celery_beat.models import PeriodicTask

        from common.celery.utils import (
            create_or_update_celery_periodic_tasks,
            delete_celery_periodic_task,
            disable_celery_periodic_task,
            get_celery_periodic_task,
        )

        create_or_update_celery_periodic_tasks({"test-lifecycle-task": {"task": "x", "interval": 60}})
        disable_celery_periodic_task("test-lifecycle-task")
        assert get_celery_periodic_task("test-lifecycle-task").enabled is False
        delete_celery_periodic_task("test-lifecycle-task")
        assert get_celery_periodic_task("test-lifecycle-task") is None
        assert not PeriodicTask.objects.filter(name="test-lifecycle-task").exists()
