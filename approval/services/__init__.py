# -*- coding:utf-8 -*-
"""``approval.services`` 兼容层。

契约门面已迁至 :mod:`approval.contracts`；本包只做 PEP 562 转发，避免存量
二开 ``from approval.services import ...`` 立即失效。业务服务模块后续落在
本包下，不要在这里重新实现契约门面。
"""

from typing import Any

# 纯 PEP 562 惰性转发：不做模块级 ``from ..contracts import *``——contracts 模块级
# 引用本包子包（如 services.approval 的协议常量），模块级转发会与之成环
# （contracts → services.approval → services/__init__ → contracts）。


def __getattr__(name: Any) -> Any:
    from .. import contracts

    return getattr(contracts, name)


def __dir__() -> list[str]:
    from .. import contracts

    return dir(contracts)
