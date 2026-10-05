#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task 域：异步任务记录 / 导入导出下载中心 / 定时任务管理 / 出站 Webhook。

自 system app 按域拆分迁入；/api/system/* 路由路径、system: 视图名、celery
任务名（system.tasks.* / task.webhook_tasks.*）全部零变化。
"""
