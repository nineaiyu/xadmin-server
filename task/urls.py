#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : urls
"""task 域路由：下载中心 / 定时任务管理 / 任务中心 / 出站 Webhook。

URL 前缀与 app 对齐：``/api/task/...``（定时任务原 ``tasks/`` 注册层是域前缀
的重复，已拍平为 ``/api/task/periodic`` 等）；Menu.path 权限点、前端 API 层、
模块裁剪 ModuleSpec 的 routes 正则已同步平移。
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

app_name = "task"

router = SimpleRouter(False)

# 导出下载中心
router.register("exports", ExportRecordViewSet, basename="export_record")
# 导入记录（下载中心「导入记录」页签）
router.register("imports", ImportRecordViewSet, basename="import_record")
# 导入列映射模板（个人 / 全局共享，导入弹窗内维护，无独立页面）
router.register("import-templates", ImportTemplateViewSet, basename="import_template")
# 定时任务管理（django_celery_beat）
router.register("periodic", PeriodicTaskViewSet, basename="periodic_task")
router.register("crontab", CrontabScheduleViewSet, basename="crontab_schedule")
router.register("executions", TaskExecutionViewSet, basename="task_execution")
router.register("interval", IntervalScheduleViewSet, basename="interval_schedule")
# 任务中心：三类记录统一列表 + 取消 / 重跑
router.register("unified", SystemTaskCenterViewSet, basename="task_center")
# 出站 Webhook
router.register("webhooks/subscriptions", WebhookSubscriptionViewSet, basename="webhook-subscription")
router.register("webhooks/deliveries", WebhookDeliveryViewSet, basename="webhook-delivery")

urlpatterns = router.urls
