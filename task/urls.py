#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : urls
"""task 域路由：下载中心 / 定时任务管理 / 任务中心 / 出站 Webhook。

不设 app_name：经 system/urls.py 同前缀挂载并入 system 命名空间（ADR-057 D1.2
口径）——/api/system/* 路径、system: 视图名、权限点与 menu.json 全部零变化。
"""

from rest_framework.routers import SimpleRouter

from task.views.admin.export import ExportRecordViewSet
from task.views.admin.import_ import ImportRecordViewSet, ImportTemplateViewSet
from task.views.task import (
    CrontabScheduleViewSet,
    IntervalScheduleViewSet,
    PeriodicTaskViewSet,
    TaskExecutionViewSet,
)
from task.views.task_center import SystemTaskCenterViewSet
from task.views.webhook import WebhookDeliveryViewSet, WebhookSubscriptionViewSet

router = SimpleRouter(False)

# 导出下载中心
router.register("exports", ExportRecordViewSet, basename="export_record")
# 导入记录（下载中心「导入记录」页签）
router.register("imports", ImportRecordViewSet, basename="import_record")
# 导入列映射模板（个人 / 全局共享，导入弹窗内维护，无独立页面）
router.register("import-templates", ImportTemplateViewSet, basename="import_template")
# 定时任务管理（django_celery_beat）
router.register("tasks/periodic", PeriodicTaskViewSet, basename="periodic_task")
router.register("tasks/crontab", CrontabScheduleViewSet, basename="crontab_schedule")
router.register("tasks/executions", TaskExecutionViewSet, basename="task_execution")
router.register("tasks/interval", IntervalScheduleViewSet, basename="interval_schedule")
# 任务中心：三类记录统一列表 + 取消 / 重跑
router.register("tasks/unified", SystemTaskCenterViewSet, basename="task_center")
# 出站 Webhook
router.register("webhooks/subscriptions", WebhookSubscriptionViewSet, basename="webhook-subscription")
router.register("webhooks/deliveries", WebhookDeliveryViewSet, basename="webhook-delivery")

urlpatterns = router.urls
