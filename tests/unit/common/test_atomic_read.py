# -*- coding: utf-8 -*-
"""读请求免 ATOMIC_REQUESTS 守护测试。

覆盖：白名单判定矩阵（方法 / action / 视图集退出开关 / 非 DRF 视图）、handler 混入
行为（命中豁免则不解包事务）、contextvar 生命周期（请求结束复位，防串请求）、
总开关回退、ASGI/WSGI 入口确实混入。
"""

import pytest
from django.test import override_settings

from common.core import atomic_read
from common.core.atomic_read import SafeMethodAtomicSkipMixin, is_read_only_request


class _View:
    """模拟 DRF as_view 产物：actions 映射 + 可选视图集类。"""

    def __init__(self, action="list", cls=None):
        self.actions = {"get": action, "post": "create"}
        self.cls = cls


class _Request:
    def __init__(self, method="GET"):
        self.method = method


@pytest.mark.parametrize(
    ("method", "action", "expected"),
    [
        ("GET", "list", True),
        ("HEAD", "list", True),
        ("GET", "retrieve", True),
        ("GET", "search_columns", True),
        ("GET", "suggestions", True),
        ("POST", "list", False),
        ("DELETE", "retrieve", False),
        ("OPTIONS", "retrieve", False),  # 预检/元数据请求不在豁免面（收益可忽略）
        # 自定义 GET action 可能带写库副作用（导出落记录 / 字段同步写行）→ 不豁免
        ("GET", "export_data", False),
        ("GET", "choices_dict", False),
        ("GET", "history", False),
    ],
)
def test_read_only_action_matrix(method, action, expected):
    assert is_read_only_request(_Request(method), _View(action)) is expected


def test_non_drf_view_not_skipped():
    """admin / 健康探针等无 actions 的视图不参与豁免。"""

    class PlainView:
        pass

    assert is_read_only_request(_Request("GET"), PlainView()) is False


def test_viewset_can_opt_out():
    class AtomicViewSet:
        force_atomic_requests = True

    assert is_read_only_request(_Request("GET"), _View("list", cls=AtomicViewSet)) is False


class _BaseHandler:
    def make_view_atomic(self, view):
        return ("wrapped", view)

    def _get_response(self, request):
        return request

    async def _get_response_async(self, request):
        return request


class _Handler(SafeMethodAtomicSkipMixin, _BaseHandler):
    pass


def test_handler_skips_atomic_for_read_action():
    view = _View("list")
    token = atomic_read._current_request.set(_Request("GET"))
    try:
        assert _Handler().make_view_atomic(view) is view
    finally:
        atomic_read._current_request.reset(token)


def test_handler_keeps_atomic_without_request_context():
    """无上下文（管理命令 / 后台任务直接调用视图）保持原语义。"""
    view = _View("list")
    assert _Handler().make_view_atomic(view) == ("wrapped", view)


def test_handler_keeps_atomic_for_write_request():
    token = atomic_read._current_request.set(_Request("POST"))
    try:
        view = _View("list")
        assert _Handler().make_view_atomic(view) == ("wrapped", view)
    finally:
        atomic_read._current_request.reset(token)


@override_settings(ATOMIC_REQUESTS_SKIP_READ_ACTIONS=False)
def test_switch_disables_skip():
    token = atomic_read._current_request.set(_Request("GET"))
    try:
        view = _View("list")
        assert _Handler().make_view_atomic(view) == ("wrapped", view)
    finally:
        atomic_read._current_request.reset(token)


def test_response_wrappers_reset_contextvar():
    """请求结束必须复位（同步执行器复用线程，残留会污染下一个请求）。"""
    import asyncio

    handler = _Handler()
    handler._get_response(_Request("GET"))
    assert atomic_read._current_request.get() is None
    asyncio.run(handler._get_response_async(_Request("GET")))
    assert atomic_read._current_request.get() is None


def test_entrypoints_use_skip_mixin():
    """ASGI（生产/daphne/uvicorn）与 WSGI 入口都必须混入豁免。"""
    from server.asgi import django_asgi_app
    from server.wsgi import application

    assert isinstance(django_asgi_app, SafeMethodAtomicSkipMixin)
    assert isinstance(application, SafeMethodAtomicSkipMixin)
