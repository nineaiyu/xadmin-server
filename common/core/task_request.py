#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出执行链：任务内请求与视图上下文（显式构造，不重放 WSGIRequest）。

历史实现从 ``request.META`` 拼 WSGI environ 让 ``WSGIRequest`` 重新解析（ADR-036 登记的
A4 风险：绕过 middleware、强依赖 DRF 内部字段、框架升级易碎）。本模块把这条链路的输入
**显式化**，并把「视图 action 执行依赖的契约」集中到唯一装配点：

- ``build_task_request``：显式给出 method / path / 查询串 / body / content-type / 提交者，
  构造最小 ``HttpRequest``（不再拼 environ）；提交者经 DRF ``_force_auth_user`` 直通
  （``ForcedAuthentication``，任务排队超过 access token 寿命也不会认证失败）；
- ``bind_view_task_context``：装配并文档化五个契约——
  1. ``view.request``：DRF Request（serializer 字段权限、filterset 与数据权限上下文读它）；
  2. ``view.action``：serializer 行为分支（如创建时密码规则）；
  3. ``view.kwargs``：detail action 路由参数（本链路为空 dict）；
  4. ``view.format_kwarg = None``：``get_serializer_context`` 依赖（漏设直接 AttributeError）；
  5. ``set_current_request``：creator 信号赋值 + 操作审计 request_uuid，由调用方绑定并在出口清理
     （``server.utils.set_current_request``）；
- 每任务构造独立请求对象：字段权限的关联对象 memo（``request._related_memo``）按请求隔离。

契约由 ``tests/unit/system/test_import_export_execution_context.py`` 以 spy 视图集守护。
"""

from io import BytesIO
from urllib.parse import urlencode

from django.http import HttpRequest, QueryDict
from rest_framework.request import Request

TASK_SERVER_NAME = "xadmin"


def _query_string(query) -> str:
    """查询串归一：dict → urlencode（doseq 保留多值），字符串原样透传（分片任务从 META 取）。"""
    if isinstance(query, str):
        return query
    return urlencode(query or {}, doseq=True)


def build_task_request(
    *,
    method: str,
    path: str,
    query_params: dict | str | None = None,
    body: bytes = b"",
    content_type: str = "application/json",
    user=None,
    request_uuid: str | None = None,
) -> HttpRequest:
    """构造任务内请求：显式字段 + 可选提交者（``_force_auth_user`` 直通）。"""
    body = body or b""
    query_string = _query_string(query_params)
    request = HttpRequest()
    request.method = method.upper()
    request.path = path or "/"
    request.path_info = request.path
    request.META = {
        "REQUEST_METHOD": request.method,
        "PATH_INFO": request.path,
        "QUERY_STRING": query_string,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": TASK_SERVER_NAME,
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": TASK_SERVER_NAME,
    }
    request.GET = QueryDict(query_string)
    request.content_type = content_type
    request._stream = BytesIO(body)
    request._read_started = False
    if request_uuid:
        # 审计链路：middleware 缺席时由任务显式携带 X-Request-Id 同源值
        request.request_uuid = request_uuid
    if user is not None:
        # 两个读法都要满足：任务汇总/通知直接读 request.user；DRF 侧认 _force_auth_user
        request.user = user
        request._force_auth_user = user
    return request


def bind_view_task_context(view, request: HttpRequest, *, action: str, kwargs: dict | None = None) -> Request:
    """把任务请求绑定到视图实例（替代 ``view.request/action/kwargs/format_kwarg`` 的手工拼装）。

    返回绑定后的 DRF Request（调用方如需再挂 thread-local 用 ``task_request_scope``）。
    """
    drf_request = Request(request, parsers=[])
    user = getattr(request, "user", None)
    if user is not None:
        # 显式直通，避免触发认证链（cookie/令牌过期场景）
        drf_request.user = user
    view.request = drf_request
    view.action = action
    view.kwargs = kwargs or {}
    view.format_kwarg = None
    return drf_request
