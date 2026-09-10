#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批拦截点（装饰器形式）。

用法（挂在需要审批的 action 上，DRF 在权限校验之后才执行 handler，装饰器晚于权限生效）：

    @ApprovalRequired()
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        ...

是否真正拦截由系统配置 APPROVAL_REQUIRED_PATHS（路径正则清单，默认空 = 休眠，
渐进启用）决定，与 SENSITIVE_OPERATION_PATHS 同口径。命中时未携带令牌则建
PENDING 审批单并返回 412 + 业务码 1002（type=approval_required），业务代码不执行；
携带令牌则消费校验（一次性、有效期、指纹一致）后放行。判定/建单/消费逻辑在
system/utils/approval.py（common 层惰性导入，跨 app 门禁合规）。
"""

import functools

from common.utils import get_logger

logger = get_logger(__name__)


class ApprovalRequired(object):
    """敏感操作审批装饰器（显式挂载，全局清单控制启停）。"""

    def __init__(self, enabled=True):
        # enabled 预留按需硬开关（如调试期临时摘除），默认恒真
        self.enabled = enabled

    def __call__(self, func):
        @functools.wraps(func)
        def wrapper(view_instance, request, *args, **kwargs):
            if self.enabled:
                from system.utils.approval import process_approval

                response = process_approval(view_instance, request)
                if response is not None:
                    return response
            return func(view_instance, request, *args, **kwargs)

        return wrapper
