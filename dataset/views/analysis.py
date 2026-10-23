#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏与报表视图（兼容再导出）。

定义已按域拆到同 app 的 ``views/screen.py`` / ``views/report.py`` 与
``views/analysis_common.py``；模板版本领域逻辑下沉到 ``services/screen.py``。
本模块保留历史 import 面，路由与测试无需改动。
"""

from dataset.services.screen import _version_keep, snapshot_template_version, version_keep
from dataset.views.analysis_common import (
    BaseAnalysisViewSet,
    _creator_guard,
    _supports_visibility,
    _visible_queryset,
)
from dataset.views.report import ReportFilter, ReportViewSet
from dataset.views.screen import (
    ScreenClipboardViewSet,
    ScreenFilter,
    ScreenTemplateFilter,
    ScreenTemplateViewSet,
    ScreenViewSet,
)

__all__ = [
    "BaseAnalysisViewSet",
    "ReportFilter",
    "ReportViewSet",
    "ScreenClipboardViewSet",
    "ScreenFilter",
    "ScreenTemplateFilter",
    "ScreenTemplateViewSet",
    "ScreenViewSet",
    "_creator_guard",
    "_supports_visibility",
    "_version_keep",
    "_visible_queryset",
    "snapshot_template_version",
    "version_keep",
]
