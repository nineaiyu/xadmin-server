# -*- coding: utf-8 -*-
"""视图集元数据能力守护：filterset 与 search-fields 元数据必须成对出现。

RePlusPage 搜索区的字段来自 search-fields 元数据（随列表响应内联下发）。视图集
若声明了 filterset_class（支持服务端过滤）却未混入 SearchFieldsAction，搜索字段
元数据整体缺失，页面就会出现「只有搜索/重置按钮、没有任何搜索输入框」的空搜索区
——流程审批页曾因 ApprovalInstanceViewSet 手动挑选混入时漏掉该 Action 触发。
"""

import pytest
from django.urls import get_resolver

pytestmark = pytest.mark.django_db


def _iter_viewset_classes():
    def walk(patterns, out):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, out)
            elif hasattr(pattern, "callback") and hasattr(pattern.callback, "cls"):
                out.add(pattern.callback.cls)

    views = set()
    walk(get_resolver().url_patterns, views)
    return views


def test_viewsets_with_filterset_expose_search_fields():
    missing = []
    for view in _iter_viewset_classes():
        if getattr(view, "filterset_class", None) is None:
            continue
        # 无 SearchFieldsAction 混入时 search_fields 方法不存在（URL 与内联元数据同时缺失）
        if not hasattr(view, "search_fields"):
            missing.append(f"{view.__module__}.{view.__name__}")
    assert not missing, (
        f"声明了 filterset_class 的视图集必须混入 SearchFieldsAction，否则前端搜索区没有任何输入框: {missing}"
    )
