# -*- coding: utf-8 -*-
"""common/celery/routing.py 单元测试（Celery 队列路由声明式收集）。

约定：应用在自身 config.py 声明 ``TASK_ROUTES = {"app.tasks.x": "heavy"}``，
可调用路由应用声明优先、框架内置兜底，二开无需改 server/settings/libs.py。
"""

import pytest

from common.celery import routing
from common.celery.routing import BUILTIN_TASK_ROUTES, celery_task_route


@pytest.fixture(autouse=True)
def _clear_cache():
    """收集结果带进程级缓存，用例间必须清空。"""
    routing._app_task_routes.cache_clear()
    yield
    routing._app_task_routes.cache_clear()


def test_builtin_heavy_route_fallback():
    """内置 heavy 路由兜底：通用异步作业与 Office 预览进 heavy 队列。"""
    assert celery_task_route("common.tasks.background_task_view_set_job") == {"queue": "heavy"}
    assert celery_task_route("system.tasks.convert_office_preview_task") == {"queue": "heavy"}


def test_unknown_task_returns_none():
    """未声明的任务返回 None（celery 语义：走默认队列）。"""
    assert celery_task_route("app.tasks.not_declared") is None


def test_callable_signature_matches_celery_query_router():
    """celery query_router 以 router(name, args, kwargs, options, task=task_type) 调用。"""
    assert celery_task_route("common.tasks.background_task_view_set_job", (), {}, {}, task=None) == {"queue": "heavy"}


def test_app_declared_route_overrides_builtin(monkeypatch):
    """应用声明优先于内置表；str 简写与 dict 全写均支持。"""

    class _FakeConfig:
        TASK_ROUTES = {
            "demo.tasks.heavy_job": "heavy",  # str 简写
            "demo.tasks.custom": {"queue": "custom-queue"},  # dict 全写
        }

    import types

    fake_module = types.ModuleType("demo.config")
    fake_module.TASK_ROUTES = _FakeConfig.TASK_ROUTES
    real_import = __import__("importlib").import_module

    def fake_import(name):
        if name == "demo.config":
            return fake_module
        return real_import(name)

    monkeypatch.setattr(routing, "import_module", fake_import)
    assert celery_task_route("demo.tasks.heavy_job") == {"queue": "heavy"}
    assert celery_task_route("demo.tasks.custom") == {"queue": "custom-queue"}
    # 内置表不被应用声明覆盖丢失
    assert celery_task_route("system.tasks.convert_office_preview_task") == {"queue": "heavy"}


def test_builtin_registry_untouched_by_app_declaration(monkeypatch):
    """应用声明只影响查找结果，不得污染内置路由表（防跨测试状态泄漏）。"""
    import types

    fake_module = types.ModuleType("demo.config")
    fake_module.TASK_ROUTES = {"common.tasks.background_task_view_set_job": {"queue": "hijack"}}
    real_import = __import__("importlib").import_module

    def fake_import(name):
        return fake_module if name == "demo.config" else real_import(name)

    monkeypatch.setattr(routing, "import_module", fake_import)
    # 应用声明优先——查找命中声明值，但内置表对象保持原样
    assert celery_task_route("common.tasks.background_task_view_set_job") == {"queue": "hijack"}
    assert BUILTIN_TASK_ROUTES["common.tasks.background_task_view_set_job"] == {"queue": "heavy"}
