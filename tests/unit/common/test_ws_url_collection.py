# -*- coding: utf-8 -*-
"""common/core/utils.py::collect_app_ws_urls 单元测试（WS 路由自动收集约定）。

约定：<app>/routing.py 暴露 urlpatterns 即自动接入 asgi，无需改工程层文件。
"""

import pytest

from common.core.utils import collect_app_ws_urls


def _route_paths(patterns):
    return [str(p.pattern) for p in patterns]


def test_collects_builtin_app_routing():
    """message / system 的 routing.urlpatterns 全量收集。"""
    paths = _route_paths(collect_app_ws_urls())
    assert any("ws/chat" in p for p in paths), f"message 路由未收集: {paths}"
    assert any("ws/system/monitor" in p for p in paths), f"system 路由未收集: {paths}"
    assert any("ws/message" in p for p in paths)
    assert any("ws/tasks/log" in p for p in paths)


def test_deterministic_across_calls():
    """INSTALLED_APPS 顺序稳定 → 收集结果可重复（三方 app 无 routing 不影响）。"""
    first = collect_app_ws_urls()
    second = collect_app_ws_urls()
    assert len(first) == len(second) >= 5  # message 2 + system 3


def test_missing_routing_module_skipped():
    """应用无 routing.py：静默跳过（收集器不炸，其余应用照常收集）。"""
    # 不 mock 任何行为：真实环境必然存在无 routing.py 的已安装应用（三方库/纯 REST app）
    assert isinstance(collect_app_ws_urls(), list)


def test_routing_internal_import_error_raised(monkeypatch):
    """routing.py 存在但内部 import 失败必须抛错（仅「无 routing.py」被吞）。"""

    from common.core import utils as ws_utils

    def fake_import(name):
        if name.endswith(".routing"):
            # 模拟 routing.py 内部 from . import consumers 失败：
            # ModuleNotFoundError 的 name 指向缺失的子模块而非 routing 本身
            app = name[: -len(".routing")]
            raise ModuleNotFoundError(f"No module named '{app}.consumers'", name=f"{app}.consumers")
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(ws_utils, "import_module", fake_import)
    with pytest.raises(ModuleNotFoundError):
        collect_app_ws_urls()
