#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典消费端工具：get_dict_items 带缓存，字典变更信号失效。"""

from django.apps import apps
from django.core.cache import cache
from django.utils import translation

DICT_CACHE_PREFIX = "data_dict_"
DICT_CACHE_TIMEOUT = 300


def get_dict_items(code):
    """取某字典类型下的启用字典项 [{label, value, color}]，5 分钟缓存。

    供业务表单 / 前端下拉消费；缓存由 DataDict 的 post_save/pre_delete 信号失效
    （invalid_dict_cache）。缓存存语言无关的原始行，返回时按当前语言本地化：
    en 环境优先 label_en（未维护英文标签时回落 label）。
    """
    data_dict_model = apps.get_model("system", "DataDict")

    def _load():
        return [
            {"label": item.label, "label_en": item.label_en, "value": item.value, "color": item.color}
            for item in data_dict_model.objects.filter(
                parent__code=code, parent__is_active=True, is_active=True
            ).order_by("sort", "created_time")
        ]

    use_en = (translation.get_language() or "").startswith("en")
    return [
        {
            "label": (row["label_en"] or row["label"]) if use_en else row["label"],
            "value": row["value"],
            "color": row["color"],
        }
        for row in cache.get_or_set(f"{DICT_CACHE_PREFIX}{code}", _load, DICT_CACHE_TIMEOUT)
    ]


def invalid_dict_cache(code=None):
    """失效字典缓存；code 为空时清全部字典缓存（字典类型删除/批量变更场景）。"""
    if code:
        cache.delete(f"{DICT_CACHE_PREFIX}{code}")
        return
    data_dict_model = apps.get_model("system", "DataDict")
    codes = list(data_dict_model.objects.filter(parent=None).values_list("code", flat=True))
    if codes:
        cache.delete_many([f"{DICT_CACHE_PREFIX}{item}" for item in codes])
