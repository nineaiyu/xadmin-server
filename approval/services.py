#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流域对外契约门面（``approval.services``）。

框架层（common）经此消费审批能力：跨 app 门禁规定 common 只允许
``from <app>.services import ...`` 模块级引用业务 app。业务 app 之间的
惰性（函数级）引用可直接指向 ``approval.utils.*``（官方逃生门）。
"""

from common.utils import get_logger

logger = get_logger(__name__)

__all__ = ["process_approval"]


def process_approval(view_instance, request):
    """审批流拦截入口（approval.utils.approval 契约导出）。"""
    from approval.utils.approval import process_approval as _process

    return _process(view_instance, request)
