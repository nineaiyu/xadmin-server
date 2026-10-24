#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""周期任务服务：任务注册、立即执行派发、主键清洗与克隆命名。

从 ``task.views.task_periodic`` 下沉：这些函数含 Celery 派发、进程级注册状态与
命名推导，放在视图模块会让视图同时承担业务实现。视图侧保留同名再导出，
``task.views.task`` 的既有再导出路径不变。
"""

import json
import threading
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_celery_beat.models import PeriodicTask

from server.celery import app
from task.models.task import TaskExecution

# 开销大，web 进程（不启动 worker，任务模块按需懒加载）只在首个触发点做一次，
# 后续请求直接复用 app.tasks。新装 app / 新增任务模块本就要求进程重启才生效，
# 绕过进程内缓存的场景以 force=True 显式重扫。
_autodiscover_lock = threading.Lock()
_autodiscovered = False


def ensure_tasks_registered(force: bool = False) -> None:
    """确保业务任务完成一次全量注册（进程内一次，并发下加锁防重复扫描）。"""
    global _autodiscovered
    if _autodiscovered and not force:
        return
    with _autodiscover_lock:
        if _autodiscovered and not force:
            return
        # 进程内可能已零散注册部分业务任务但缺 system.tasks 等，故强制补齐；
        # 扫描抛错时不置位，下次请求重试（与逐请求扫描的重试语义一致）
        app.autodiscover_tasks(force=True)
        _autodiscovered = True


def _dispatch_periodic_run(instance: Any) -> Any:
    """为周期任务派发一次立即执行，返回新建的 TaskExecution。

    Raises:
        ValueError: 任务未注册、不在可手动执行白名单，或 args/kwargs 不是合法 JSON。
    """
    from task.utils.task_whitelist import is_task_runnable

    if not is_task_runnable(instance.task):
        # 白名单在执行侧再拦一道：只挡写入不挡执行，存量任务可绕过
        raise ValueError(
            _('Task "{}" is not allowed for manual execution (not in the runnable whitelist)').format(instance.task)
        )
    if instance.task not in app.tasks:
        # 未注册时补一次全量注册（进程内只扫一次，不随未注册任务的重试反复全量 import）
        ensure_tasks_registered()
    if instance.task not in app.tasks:
        raise ValueError(_('Task "{}" is not registered').format(instance.task))
    try:
        args = json.loads(instance.args or "[]")
        kwargs = json.loads(instance.kwargs or "{}")
    except (json.JSONDecodeError, TypeError):
        raise ValueError(_("Task arguments are not valid JSON")) from None
    execution = TaskExecution.objects.create(
        name=instance.task,
        periodic_task=instance,
        args=args,
        kwargs=kwargs,
    )

    # on_commit 保证记录先落库，publisher 进程的 after_task_publish 才能命中既有记录
    def _dispatch() -> None:
        app.send_task(
            instance.task,
            args=args,
            kwargs=kwargs,
            task_id=str(execution.pk),
            headers={"periodic_task_name": instance.name},
        )

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # 测试/E2E：task_always_eager 对 send_task 无效（AlwaysEagerIgnored，
        # 消息投 memory broker 无人消费，执行记录永远停在 PENDING）；改走
        # apply 同步执行，触发 prerun/postrun 信号完成状态流转
        app.tasks[instance.task].apply(
            args=args,
            kwargs=kwargs,
            task_id=str(execution.pk),
            headers={"periodic_task_name": instance.name},
        )
    else:
        transaction.on_commit(_dispatch)
    return execution


def _clean_pks(pks: Any) -> list[Any]:
    """清洗主键列表：PeriodicTask 主键为整型，非法值直接忽略而非查询报错"""
    valid = []
    for pk in pks or []:
        try:
            valid.append(int(pk))
        except (TypeError, ValueError):
            continue
    return valid


def _next_available_clone_name(source_name: str) -> str:
    """取克隆名的第一个可用候选：一次查询取回同前缀既有名，内存推导后缀。

    命名格式与逐次探测时保持一致：首选「{source_name}-copy」，被占用则依次
    尝试「{source_name}-copy-2」「-copy-3」……（不存在「-copy-1」形态）。
    同前缀但非「-{数字}」后缀的名字（如手工改名的「-copy-备份」）不参与
    后缀占位；「-copy-1」也不阻塞首选名。同名密集时避免逐候选一次 EXISTS。
    """
    base_name = f"{source_name}-copy"
    base_taken = False
    taken_suffixes = set()
    for existing in PeriodicTask.objects.filter(name__startswith=base_name).values_list("name", flat=True):
        suffix = existing[len(base_name) :]
        if suffix == "":
            base_taken = True
        elif suffix.startswith("-") and suffix[1:].isdecimal():
            # isdecimal 而非 isdigit：上标数字（如「²」）.isdigit 为真但 int() 抛错
            taken_suffixes.add(int(suffix[1:]))
    if not base_taken:
        return base_name
    index = 2
    while index in taken_suffixes:
        index += 1
    return f"{base_name}-{index}"


def _clone_periodic_task(instance: PeriodicTask) -> PeriodicTask:
    """克隆周期任务：复制调度与参数，生成唯一名称，默认停用（避免克隆即执行）"""
    name = _next_available_clone_name(instance.name)
    clone = PeriodicTask.objects.get(pk=instance.pk)
    clone.pk = None
    clone.name = name
    clone.enabled = False
    clone.last_run_at = None
    clone.total_run_count = 0
    clone.save()  # 触发 django_celery_beat 信号，beat 感知新任务
    return clone
