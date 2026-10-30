#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流域对外契约门面（``approval.contracts``）。

框架层（common）经此消费审批能力：跨 app 门禁规定 common 只允许
``from <app>.contracts import ...`` 模块级引用业务 app。业务 app 之间的惰性
（函数级）引用虽是官方逃生门，但**引擎内部符号**（models / serializers /
services 的具体函数）经此收敛成显式契约面——调用方只见稳定入口，引擎内部
重构不再需要逐 app 排查引用（首个消费者：dataset 动态表单 × 审批链路）。

契约分组：

- 模型访问：``ApprovalFlow`` / ``ApprovalInstance`` / ``ApprovalFlowVersion``
  （定义类资源的跨域读，如表单绑定流程 / 版本快照）；
- 流程实例：``create_instance``（发起）、``instance_status_choices``（终态口径）；
- 终态回调：``register_on_approved``（审批通过后动作注册）；
- 审批轨迹：``serialize_instance_tasks``（节点任务序列化，供表单/详情展示）；
- 敏感操作审批协议面：``APPROVAL_HEADER`` / ``APPROVAL_QUERY_PARAM`` 常量与
  ``consume_approval`` / ``create_approval`` / ``find_active_pending`` /
  ``get_request_params`` / ``pending_response`` / ``process_approval``。
"""

from typing import Any

from approval.models.approval import (  # noqa: F401 跨域模型访问经契约面再导出
    ApprovalFlow as ApprovalFlow,
)
from approval.models.approval import (
    ApprovalFlowVersion as ApprovalFlowVersion,  # noqa: F401 显式再导出（PEP 484 语义）
)
from approval.models.approval import (
    ApprovalInstance as ApprovalInstance,  # noqa: F401 显式再导出（PEP 484 语义）
)
from approval.services.approval import (  # noqa: F401 门禁协议常量（纯字符串，模块级安全）
    APPROVAL_HEADER as APPROVAL_HEADER,
)
from approval.services.approval import (
    APPROVAL_QUERY_PARAM as APPROVAL_QUERY_PARAM,  # noqa: F401 显式再导出（PEP 484 语义）
)

__all__ = [
    "APPROVAL_HEADER",
    "APPROVAL_QUERY_PARAM",
    "ApprovalFlow",
    "ApprovalFlowVersion",
    "ApprovalInstance",
    "consume_approval",
    "create_approval",
    "create_instance",
    "find_active_pending",
    "get_request_params",
    "instance_status_choices",
    "pending_response",
    "process_approval",
    "register_on_approved",
    "serialize_instance_tasks",
]


def create_instance(*args: Any, **kwargs: Any) -> Any:
    """发起流程实例（契约再导出；签名见 ``approval.services.approval_flow.create_instance``）。"""
    from approval.services.approval_flow import create_instance as _create

    return _create(*args, **kwargs)


def instance_status_choices() -> Any:
    """实例状态枚举表（``ApprovalInstance.Status.choices``，终态口径判断用）。"""
    from approval.models.approval import ApprovalInstance as _Instance

    return _Instance.Status.choices


def register_on_approved(*args: Any, **kwargs: Any) -> Any:
    """登记「审批通过后动作」（契约再导出；签名见 ``approval.services.approval``）。"""
    from approval.services.approval import register_on_approved as _register

    return _register(*args, **kwargs)


def serialize_instance_tasks(tasks: Any, context: Any) -> Any:
    """审批轨迹序列化（节点任务 → 展示口径：状态/审批人/意见/时间/加签/委托来源）。"""
    from approval.serializers.approval_flow import ApprovalNodeTaskSerializer

    return ApprovalNodeTaskSerializer(tasks, many=True, context=context).data


def consume_approval(*args: Any, **kwargs: Any) -> Any:
    """消费一次性审批令牌（契约再导出；语义见 ``approval.services.approval`` 模块注释）。"""
    from approval.services.approval import consume_approval as _consume

    return _consume(*args, **kwargs)


def create_approval(*args: Any, **kwargs: Any) -> Any:
    """创建敏感操作审批单（契约再导出）。"""
    from approval.services.approval import create_approval as _create

    return _create(*args, **kwargs)


def find_active_pending(*args: Any, **kwargs: Any) -> Any:
    """查同内容的在途审批单（契约再导出）。"""
    from approval.services.approval import find_active_pending as _find

    return _find(*args, **kwargs)


def get_request_params(*args: Any, **kwargs: Any) -> Any:
    """请求指纹参数提取（契约再导出）。"""
    from approval.services.approval import get_request_params as _params

    return _params(*args, **kwargs)


def pending_response(*args: Any, **kwargs: Any) -> Any:
    """在途/待审批的 412 响应构造（契约再导出）。"""
    from approval.services.approval import pending_response as _pending

    return _pending(*args, **kwargs)


def process_approval(*args: Any, **kwargs: Any) -> Any:
    """审批流拦截入口（common 的 ApprovalRequired 装饰器消费）。"""
    from approval.services.approval import process_approval as _process

    return _process(*args, **kwargs)
