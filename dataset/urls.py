#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据分析与动态表单路由：经 system/urls.py 以 ``path("", include("dataset.urls"))`` 挂载。

URL 前缀保持 ``/api/system/...``（Menu.path 权限点、前端路由、模块裁剪
ModuleSpec 的 routes 正则均以此为键，拆分不改路径）；不设 app_name，
视图名继续落在 system 命名空间下，与拆分前完全一致。
"""

from rest_framework.routers import SimpleRouter

from dataset.views.analysis import ReportViewSet, ScreenViewSet
from dataset.views.dataset import DashboardViewSet as DataDashboardViewSet
from dataset.views.dataset import DatasetViewSet
from dataset.views.dform import DynamicFormSubmissionViewSet, DynamicFormViewSet

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
