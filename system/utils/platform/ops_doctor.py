#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""运维可靠性自检条目（doctor 消费；口径与 security_doctor 同构）。

两项均为「异步消息可靠性战役」（审查 BE-M2 / BE-L6）的部署校验面：
1. Redis AOF 持久化：该实例同时是 Celery broker，RDB-only 时崩溃丢消息窗口
   分钟级——compose 已默认开启，自管 / 改造过的部署在此点名；
2. 周期任务调度存活：beat 停摆会让全部周期任务（含 Webhook 重试兜底扫描）
   无告警地集体停跑——最近一次调度距今超 1 小时或从未调度即告警。
"""

import datetime
from typing import Any


def report_redis_persistence(report: Any) -> None:
    """Redis 持久化档位自检（appendonly；FakeRedis 等不支持 CONFIG GET 时跳过）。"""
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        raw = conn.config_get("appendonly")
        enabled = str(raw.get("appendonly", "")).strip().lower() == "yes"
    except Exception as exc:  # noqa: BLE001 FakeRedis / 自定义后端不支持 CONFIG GET：跳过
        report("⚠️", "Redis 持久化", f"无法读取 appendonly 配置（跳过）：{exc}")
        return
    if enabled:
        report("✅", "Redis 持久化", "AOF 已开启（broker 消息崩溃丢失窗口 ≤1s）")
    else:
        report(
            "⚠️",
            "Redis 持久化",
            "AOF 未开启（appendonly=no）：该实例同时是 Celery broker，崩溃丢消息窗口为分钟级（RDB 快照间隔）",
            "compose 栈保持 REDIS_APPENDONLY 默认 yes；自管 Redis 显式 --appendonly yes"
            "（纯缓存实例可接受，但 broker 上的异步任务 / Webhook 消息会丢）",
        )


def _beat_findings() -> list[dict[str, Any]]:
    """周期调度存活判定：无任务/从未调度/最近调度超 1 小时三态（pass/warn）。"""
    try:
        from django_celery_beat.models import PeriodicTask
    except ImportError:  # pragma: no cover - beat 未安装
        return []
    try:
        enabled = list(PeriodicTask.objects.filter(enabled=True).values_list("name", "last_run_at"))
    except Exception as exc:  # noqa: BLE001 数据库未就绪时由数据库自检项报告
        return [{"level": "warn", "name": "周期调度", "detail": f"检查失败（跳过）：{exc}"}]
    if not enabled:
        return [{"level": "pass", "name": "周期调度", "detail": "无启用的周期任务（跳过）"}]
    last_runs = [run for _name, run in enabled if run is not None]
    if not last_runs:
        return [
            {
                "level": "warn",
                "name": "周期调度",
                "detail": f"{len(enabled)} 个启用的周期任务从未运行（beat 疑似从未调度）",
                "fix": "确认 beat 容器在运行：docker ps | grep beat；docker logs --tail 50 xadmin-celery-beat",
            }
        ]
    from django.utils import timezone

    lag = timezone.now() - max(last_runs)
    if lag > datetime.timedelta(hours=1):
        return [
            {
                "level": "warn",
                "name": "周期调度",
                "detail": f"周期任务最近一次调度距今 {int(lag.total_seconds() // 60)} 分钟（beat 疑似停摆或积压）",
                "fix": "检查 beat 容器与调度日志：docker logs --tail 50 xadmin-celery-beat；宿主负载过高时排查 worker 积压",
            }
        ]
    return [
        {
            "level": "pass",
            "name": "周期调度",
            "detail": f"{len(enabled)} 个周期任务调度正常（最近 {int(lag.total_seconds())}s 前触发）",
        }
    ]


def report_beat_schedule(report: Any) -> None:
    """周期任务调度存活自检输出（单项异常不中断整体）。"""
    for item in _beat_findings():
        mark = "✅" if item["level"] == "pass" else "⚠️"
        report(mark, item["name"], item["detail"], item.get("fix"))


def report_stale_module_periodic_tasks(report: Any, detail_limit: int) -> None:
    """任务层切片自检：停用模块的周期任务不应仍有启用条目。

    注册期由 ``create_or_update_registered_periodic_tasks`` 跳过停用模块并清理
    历史条目——本检查抓的是「改了配置还没重启」的窗口期残留，只提示不判失败。
    （自 doctor 命令文件平移至此：行数门禁下按域归组，口径不变。）
    """

    from common.celery.decorator import get_register_period_tasks
    from common.core.modules import resolve_modules
    from task.services.periodic import ensure_tasks_registered

    try:
        from django_celery_beat.models import PeriodicTask
    except ImportError:  # pragma: no cover - beat 未安装
        return
    disabled = resolve_modules().disabled
    if not disabled:
        return
    ensure_tasks_registered()
    disabled_paths = {
        detail["task"]
        for entry in get_register_period_tasks()
        for _name, detail in entry.items()
        if detail.get("module") in disabled
    }
    if not disabled_paths:
        return
    stale = list(
        PeriodicTask.objects.filter(enabled=True, task__in=disabled_paths).values_list("name", flat=True)[:detail_limit]
    )
    if stale:
        report(
            "⚠️",
            "模块裁剪",
            f"停用模块的周期任务仍启用：{'、'.join(stale)}",
            "重启进程（启动期对账会自动清理重建周期任务）",
        )
