#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：请求指纹与拦截判定。"""

import json
import re

from common.utils import get_logger

logger = get_logger(__name__)


def canonical_params(params) -> str:
    """请求体快照的规范化 JSON（排序键 + 固定默认值），用于一致性比对。"""
    return json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)


def get_request_params(request):
    """取脱敏后的请求体快照：dict 走 desensitize_body，其余（list/字符串）原样。

    multipart（"multipart/form-data" 哨兵串）场景不开放审批：文件无法进快照，
    登记为边界（拦截判定时不会比对出有效指纹）。
    """
    from common.core.middleware import desensitize_body

    body = getattr(request, "request_data", None)
    if body is None:
        body = getattr(request, "data", None)
    if isinstance(body, dict):
        return desensitize_body(body)
    if body is None:
        return {}
    return body


def get_request_object_pk(view) -> str:
    """detail 路由的对象主键（pk 兜底 id），list 路由返回 None。"""
    kwargs = getattr(view, "kwargs", None) or {}
    value = kwargs.get("pk") or kwargs.get("id")
    return str(value) if value is not None else None


def path_intercepted(path: str) -> bool:
    """全局清单判定（APPROVAL_REQUIRED_PATHS，默认空 = 审批整体休眠）。

    与 SENSITIVE_OPERATION_PATHS 同口径：正则清单命中即拦截；非法正则跳过
    并告警（不 500、不放任整个清单失效）。
    """
    from common.core.config import SysConfig

    paths = SysConfig.APPROVAL_REQUIRED_PATHS or []
    if not paths:
        return False
    matched = False
    for pattern in paths:
        if not pattern:
            continue
        try:
            if re.search(pattern, path):
                matched = True
                break
        except re.error:
            logger.warning("approval interception skipped: invalid path regex %s", pattern)
            continue
    return matched
