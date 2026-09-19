# -*- coding: utf-8 -*-
"""ViewSet 覆写带 ``@action`` 的父类方法时，装饰器（``.mapping``）不得丢失。

DRF 靠函数对象上的 ``.mapping`` 识别 extra action 并注册路由
（``ViewSetMixin.get_extra_actions``，按 ``method.mapping`` 收集）；子类覆写
``BatchDestroyAction.batch_destroy`` 这类带装饰器的方法时若忘记重新装饰，
端点会静默消失——请求落到 detail 路由（``pk="batch-destroy"``）返回 405，
对应权限点因路由不存在也不会下发给任何用户（能力缺失而非点击报错）。

本测试从 URLconf 收集全部已注册 ViewSet，沿 MRO 检查「父类中带 ``.mapping``
的方法在子类被覆写后是否仍带 ``.mapping``」。
"""

from django.urls import get_resolver
from rest_framework.viewsets import ViewSetMixin


def _collect_viewset_classes() -> set:
    """从 URLconf 收集全部已注册 ViewSet 类（含嵌套 include）。"""
    classes: set = set()

    def walk(patterns) -> None:
        for pattern in patterns:
            nested = getattr(pattern, "url_patterns", None)
            if nested is not None:
                walk(nested)
                continue
            view_cls = getattr(getattr(pattern, "callback", None), "cls", None)
            if view_cls is not None and issubclass(view_cls, ViewSetMixin):
                classes.add(view_cls)

    walk(get_resolver().url_patterns)
    return classes


def test_action_decorator_not_lost_in_overrides():
    problems = []
    for view_cls in _collect_viewset_classes():
        for base in view_cls.__mro__[1:]:
            for name, value in vars(base).items():
                if not hasattr(value, "mapping"):
                    continue
                override = view_cls.__dict__.get(name)
                if override is not None and not hasattr(override, "mapping"):
                    problems.append(f"{view_cls.__module__}.{view_cls.__name__}.{name}")
    assert problems == [], "以下覆写丢失了 @action 装饰器（路由不会注册，请求将 405）: " + ", ".join(sorted(problems))
