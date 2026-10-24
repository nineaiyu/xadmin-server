#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""``approval.utils.approval`` 兼容层（实现已迁至 ``approval.services.approval``）。

保留包级再导出，避免存量二开与外部脚本的 ``from approval.utils.approval import ...``
立即失效；新代码一律直接引用 ``approval.services.approval``。子模块路径
（如 ``approval.utils.approval.display``）经 ``__getattr__`` 惰性转发到新位置。
"""

import sys
from typing import Any

from approval.services.approval import *  # noqa: F403
from approval.services.approval import __all__ as __all__  # noqa: F401

_SUBMODULES = (
    "approved_actions",
    "approvers",
    "chains",
    "constants",
    "display",
    "lifecycle",
    "lifecycle_chain",
    "notify",
    "payload",
    "periodic",
    "queries",
    "snapshot",
)


def __getattr__(name: str) -> Any:
    import importlib

    if name in _SUBMODULES:
        module = importlib.import_module(f"approval.services.approval.{name}")
        sys.modules[f"{__name__}.{name}"] = module
        return module
    # 包内符号兜底（含 ``_emit_approval_event`` 这类 ``import *`` 不会带的私有再导出）
    from approval.services import approval as _impl

    try:
        return getattr(_impl, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
