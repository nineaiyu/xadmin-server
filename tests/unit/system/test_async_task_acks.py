# -*- coding: utf-8 -*-
"""异步导入/导出任务可靠性声明守护：acks_late 必须开启。

背景（审查修复项 BE-M1）：导入/导出在 heavy worker 执行中若 worker 崩溃 / 宿主
重启 / 30min 硬超时杀进程，早 ack 的消息直接丢失，ImportRecord / ExportRecord
将永久停在 RUNNING（终态幂等守卫已在：``run_async_import`` / ``run_async_export``
对终态记录直接跳过），因此重投是安全的。邮件 / Webhook 任务已显式声明
``acks_late=True``（tests/unit/common/test_tasks.py::test_mail_tasks_declare_acks_late），
本文件把导入/导出两任务锁进同一口径，防回退。
"""

from system.tasks import async_export_data_task, async_import_data_task


def test_async_import_task_declares_acks_late():
    assert async_import_data_task.acks_late is True


def test_async_export_task_declares_acks_late():
    assert async_export_data_task.acks_late is True
