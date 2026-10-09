#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 域：异步任务记录 / 导入导出下载中心 / 定时任务管理 / 出站 Webhook。

自 system app 按域拆分迁入；路由独立前缀挂载（``/api/task/...``），celery
任务名（system.tasks.* / task.webhook_tasks.*）保持零变化。
"""
