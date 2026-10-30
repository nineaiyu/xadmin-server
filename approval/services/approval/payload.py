#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：请求指纹与拦截判定。"""

import hashlib
import json
import re
from typing import Any

from common.core.auth.credentials import SENSITIVE_KEY_PATTERN as SENSITIVE_NAME_PATTERN
from common.core.auth.sensitive import SENSITIVE_FIELDS
from common.utils import get_logger

logger = get_logger(__name__)

# 审批快照专用掩码形态：确定性哈希（不保留明文与长度）。同一原值掩码恒等——
# 指纹比对（建单快照 vs 重发请求）因此保有比对能力；等长换值的重放会得到
# 不同哈希而被 consume 校验拦下（等长星号掩码只比得出「长度没变」）。
MASK_HASH_PREFIX = "sha256:"
MASK_HASH_LENGTH = 16

# 敏感载体键：键名本身不敏感、但值承载敏感标量的容器键（如 SystemConfig 的
# {"key": "SCIM_TOKEN", "value": "<明文>"}）；仅当同 dict 携带命中敏感名模式的
# 标量载体（key/name）时才掩码，避免把业务上的普通 value/detail 误伤。
VALUE_DETAIL_KEYS = ("value", "detail")
CARRIER_NAME_KEYS = ("key", "name")


def canonical_params(params: Any) -> str:
    """请求体快照的规范化 JSON（排序键 + 固定默认值），用于一致性比对。"""
    return json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)


def _mask_hash(value: Any) -> str:
    """敏感值掩码：sha256 前 16 位十六进制（确定性、不可逆、不保留长度）。"""
    digest = hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()[:MASK_HASH_LENGTH]
    return f"{MASK_HASH_PREFIX}{digest}"


def _mask_sensitive_value(item: Any) -> Any:
    """敏感键下的叶子掩码：容器保持结构，所有标量叶子一律哈希。

    不能按「容器原样递归」处理：``{"token": ["a", "b"]}`` 或
    ``{"password": {"old": "x"}}`` 的嵌套键名未必命中敏感名单，递归会把明文留下。
    """
    if isinstance(item, dict):
        return {key: _mask_sensitive_value(value) for key, value in item.items()}
    if isinstance(item, (list, tuple)):
        return [_mask_sensitive_value(value) for value in item]
    if item is None or item == "":
        return item
    return _mask_hash(item)


def _carrier_name_sensitive(row: dict[str, Any]) -> bool:
    """该 dict 是否以敏感名做载体（key/name 标量命中敏感名模式）。"""
    for carrier in CARRIER_NAME_KEYS:
        name = row.get(carrier)
        if isinstance(name, str) and name and SENSITIVE_NAME_PATTERN.search(name):
            return True
    return False


def desensitize_params(value: Any) -> Any:
    """审批快照统一递归脱敏：dict/list/tuple 任意嵌套统一处理。

    与操作日志的 ``desensitize_payload`` 分口径（审批快照需要指纹可比）：
    - 任意容器统一递归——顶层 list 体（批量请求）与嵌套结构不再整包明文进快照；
    - 敏感键（SENSITIVE_FIELDS）下的标量用确定性哈希掩码，不保留长度；
    - value/detail 标量：同 dict 携带敏感名载体（key/name 命中敏感名模式）时一并掩码
      （整值加密型配置的请求体形如 {"key": "SCIM_TOKEN", "value": "<明文>"}）。
    """
    if isinstance(value, dict):
        carrier_sensitive = _carrier_name_sensitive(value)
        result: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key in SENSITIVE_FIELDS:
                result[key] = _mask_sensitive_value(item)
                continue
            if carrier_sensitive and key in VALUE_DETAIL_KEYS:
                # 载体值可能是嵌套对象/数组（如 OAuth provider 列表），叶子一律掩码
                result[key] = _mask_sensitive_value(item)
                continue
            result[key] = desensitize_params(item)
        return result
    if isinstance(value, (list, tuple)):
        return [desensitize_params(item) for item in value]
    return value


def get_request_params(request: Any) -> Any:
    """取脱敏后的请求体快照：dict/list 统一递归脱敏，字符串等标量原样。

    multipart（"multipart/form-data" 哨兵串）场景不开放审批：文件无法进快照，
    登记为边界（拦截判定时不会比对出有效指纹）。
    """
    body = getattr(request, "request_data", None)
    if body is None:
        body = getattr(request, "data", None)
    if body is None:
        return {}
    return desensitize_params(body)


def get_request_object_pk(view: Any) -> str | None:
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
