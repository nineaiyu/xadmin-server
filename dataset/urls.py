#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据分析与动态表单路由：独立前缀挂载（server/urls.py ``^api/dataset/``）。

URL 前缀与 app 对齐（ADR-059）：``/api/dataset/...``；Menu.path 权限点、
前端 API 层、模块裁剪 ModuleSpec 的 routes 正则已同步平移。
"""

from rest_framework.routers import SimpleRouter

from dataset.views.analysis import ReportViewSet, ScreenViewSet
from dataset.views.dataset import DashboardViewSet as DataDashboardViewSet
from dataset.views.dataset import DatasetViewSet
from dataset.views.dform import DynamicFormSubmissionViewSet, DynamicFormViewSet

app_name = "dataset"

router = SimpleRouter(False)
router.register("datasets", DatasetViewSet, basename="dataset")
router.register("dashboards", DataDashboardViewSet, basename="dashboards")
# 大屏与定时报表
router.register("screens", ScreenViewSet, basename="screen")
router.register("reports", ReportViewSet, basename="report")
# 动态表单
router.register("dynamic-forms", DynamicFormViewSet, basename="dynamic-form")
router.register("dynamic-form-submissions", DynamicFormSubmissionViewSet, basename="dynamic-form-submission")

urlpatterns = router.urls
