#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏工具：规则加载（300s 缓存，信号失效）+ apply_mask 纯函数。

- apply_mask 为纯函数，单测主战场；
- get_mask_rules(model_label) 返回按 sort 升序的活动规则（roles 展开为 pk 列表），
  缓存由 DataMaskRule 的 post_save/post_delete/m2m_changed 信号失效（signal_handler.py）。
"""

import re

from django.apps import apps
from django.core.cache import cache

MASK_CACHE_PREFIX = "data_mask_"
MASK_CACHE_TIMEOUT = 300


def _segment(value, keep_head, keep_tail, mask_char):
    """通用分段掩码：保留前 keep_head 与后 keep_tail，中间以 mask_char 填充。
    长度不足以构成掩码区间时原样返回（避免退化成一整串星号破坏可辨识度）。
    """
    n = len(value)
    if n <= (keep_head + keep_tail) or n <= 1:
        return value
    masked_len = n - keep_head - keep_tail
    masked = mask_char * masked_len
    if keep_head:
        masked = value[:keep_head] + masked
    if keep_tail:
        masked = masked + value[n - keep_tail :]
    return masked


def _mask_email(value, keep_head, mask_char):
    local, sep, domain = value.rpartition("@")
    if not sep:
        return _segment(value, keep_head, 0, mask_char)
    return f"{_segment(local, keep_head, 0, mask_char)}{sep}{domain}"


def apply_mask(value, rule):
    """按规则对单个值脱敏。空值/非字符串/规则缺省时原样返回。

    :param rule: get_mask_rules 产出的规则 dict（mask_type/keep_head/keep_tail/mask_char/pattern）
    """
    if value is None or not isinstance(value, str) or not value:
        return value
    mask_type = rule.get("mask_type")
    keep_head = max(int(rule.get("keep_head") or 0), 0)
    keep_tail = max(int(rule.get("keep_tail") or 0), 0)
    mask_char = rule.get("mask_char") or "*"
    if mask_type == "email":
        return _mask_email(value, keep_head, mask_char)
    if mask_type == "custom":
        pattern = rule.get("pattern")
        if not pattern:
            return value
        try:
            return re.sub(pattern, lambda m: _segment(m.group(0), keep_head, keep_tail, mask_char), value)
        except re.error:
            return value
    # phone / idcard / name / bankcard 及默认路径统一走分段掩码
    return _segment(value, keep_head, keep_tail, mask_char)


def get_mask_rules(model_label):
    """取某模型的活动脱敏规则（按 sort 升序），5 分钟缓存。"""
    data_mask_model = apps.get_model("system", "DataMaskRule")

    def _load():
        queryset = data_mask_model.objects.filter(model=model_label, is_active=True).order_by("sort", "created_time")
        return [
            {
                "field": rule.field,
                "mask_type": rule.mask_type,
                "keep_head": rule.keep_head,
                "keep_tail": rule.keep_tail,
                "mask_char": rule.mask_char,
                "pattern": rule.pattern,
                "roles": [pk for pk in rule.roles.values_list("pk", flat=True)],
            }
            for rule in queryset
        ]

    return cache.get_or_set(f"{MASK_CACHE_PREFIX}{model_label}", _load, MASK_CACHE_TIMEOUT)


def invalid_mask_cache(model_label=None):
    """失效脱敏缓存；model_label 缺省时清全部（规则批量变更场景）。"""
    if model_label:
        cache.delete(f"{MASK_CACHE_PREFIX}{model_label}")
        return
    data_mask_model = apps.get_model("system", "DataMaskRule")
    models_list = list(data_mask_model.objects.values_list("model", flat=True))
    if models_list:
        cache.delete_many([f"{MASK_CACHE_PREFIX}{item}" for item in models_list])
