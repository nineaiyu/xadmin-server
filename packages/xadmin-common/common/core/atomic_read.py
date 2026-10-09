#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""读请求免事务：ATOMIC_REQUESTS 对「纯读 action」的定向豁免。

背景：``ATOMIC_REQUESTS=True`` 给每个请求包一层事务，纯读请求因此多出
BEGIN/COMMIT 两次数据库往返（容器/局域网链路实测约 1-3ms/请求，列表页与
元数据接口是调用量最大的一类）。

豁免范围刻意保守——只有**方法安全**（GET/HEAD）且 action 命中
``READ_ONLY_ACTIONS`` 白名单（DRF 标准读动作）才跳过：

- 自定义 GET action 可能带写库副作用（导出落 ExportRecord、字段同步写行、
  文件下载写访问审计等），一律保持事务语义，避免失败时留下半成品；
- 写请求（POST/PUT/PATCH/DELETE）不受影响，仍为「整请求一个事务」；
- 视图集可用 ``force_atomic_requests = True`` 显式退出豁免（纵深开关）。

实现要点（ASGI 线程模型，勿改为 thread-local）：``BaseHandler._get_response`` /
``_get_response_async`` 里 ``make_view_atomic`` 与同步视图不在同一执行上下文
（同步视图经 sync_to_async 线程敏感执行器执行），且 ASGI 事件循环上同一线程会
交替执行多个请求的协程——用 **contextvar** 绑定「当前请求」既跨执行器可见，
又天然按任务隔离（contextvars 随 Task 上下文分发）。
"""

import contextvars
from typing import Any

from common.settings_contract import kernel_setting

SAFE_METHODS = frozenset({"GET", "HEAD"})

# 只豁免「纯读」的标准动作；新增自定义读接口时不必加入——保持事务更安全，
# 白名单只服务于调用量最大且确定无副作用的列表/详情/元数据路径。
READ_ONLY_ACTIONS = frozenset({"list", "retrieve", "search_fields", "search_columns", "choices", "suggestions"})

_current_request = contextvars.ContextVar("atomic_read_current_request", default=None)


def _action_of(actions: dict[str, Any], method: str) -> Any:
    action = actions.get(method.lower())
    if action is None and method == "HEAD":
        # DRF/Django 语义：HEAD 走 GET handler（actions 可能未显式声明 head）
        action = actions.get("get")
    return action


def is_read_only_request(request: Any, view: Any) -> bool:
    """该请求是否命中「纯读 action」豁免面。"""
    if request is None or request.method not in SAFE_METHODS:
        return False
    actions = getattr(view, "actions", None)
    if not isinstance(actions, dict):
        # 非 DRF 视图（admin / 健康探针 / 第三方视图）不参与豁免
        return False
    if _action_of(actions, request.method) not in READ_ONLY_ACTIONS:
        return False
    return not getattr(getattr(view, "cls", None), "force_atomic_requests", False)


def skip_atomic_enabled() -> bool:
    """总开关（config.yml 的 ATOMIC_REQUESTS_SKIP_READ_ACTIONS，默认开）。"""
    return bool(kernel_setting("ATOMIC_REQUESTS_SKIP_READ_ACTIONS"))


class SafeMethodAtomicSkipMixin:
    """ASGI/WSGI handler 混入：纯读请求不套 ATOMIC_REQUESTS 事务。

    ``_get_response`` / ``_get_response_async`` 覆写只为把「当前请求」放进
    contextvar（基类签名不带 request，``make_view_atomic`` 拿不到），随后仍调用
    基类实现，不复制任何 Django 内部逻辑。
    """

    def make_view_atomic(self, view: Any) -> Any:
        if skip_atomic_enabled() and is_read_only_request(_current_request.get(), view):
            return view
        return super().make_view_atomic(view)  # type: ignore[misc]  # 宿主 handler 提供实现

    def _get_response(self, request: Any) -> Any:
        token = _current_request.set(request)
        try:
            return super()._get_response(request)  # type: ignore[misc]  # 宿主 handler 提供实现
        finally:
            _current_request.reset(token)

    async def _get_response_async(self, request: Any) -> Any:
        token = _current_request.set(request)
        try:
            return await super()._get_response_async(request)  # type: ignore[misc]  # 宿主 handler 提供实现
        finally:
            _current_request.reset(token)
