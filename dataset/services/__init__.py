# -*- coding:utf-8 -*-
"""``dataset.services`` 兼容层。

契约门面已迁至 :mod:`dataset.contracts`；本包只做 PEP 562 转发，避免存量
二开 ``from dataset.services import ...`` 立即失效。业务服务模块后续落在
本包下，不要在这里重新实现契约门面。
"""

from typing import Any

from ..contracts import *  # noqa: F403
from ..contracts import __all__ as __all__  # noqa: F401


def __getattr__(name: Any) -> Any:
    from .. import contracts

    return getattr(contracts, name)
