#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""``approval.utils.approval_flow`` 兼容层（实现已迁至 ``approval.services.approval_flow``）。

保留包级再导出，避免存量二开与外部脚本的 ``from approval.utils.approval_flow import ...``
立即失效；新代码一律直接引用 ``approval.services.approval_flow``。
"""

from approval.services.approval_flow import *  # noqa: F403
from approval.services.approval_flow import __all__ as __all__  # noqa: F401
