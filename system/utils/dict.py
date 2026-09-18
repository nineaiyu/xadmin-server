#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典消费端工具：get_dict_items 带缓存，字典变更信号失效。"""

from django.apps import apps
from django.core.cache import cache
from django.utils import translation

from common.utils import get_logger

logger = get_logger(__name__)

DICT_CACHE_PREFIX = "data_dict_"
DICT_CACHE_TIMEOUT = 300


def get_dict_items(code):
    """取某字典类型下的启用字典项 [{label, value, color}]，5 分钟缓存。

    供业务表单 / 前端下拉消费；缓存由 DataDict 的 post_save/pre_delete 信号失效
    （invalid_dict_cache）。缓存存语言无关的原始行，返回时按当前语言本地化：
    en 环境优先 label_en（未维护英文标签时回落 label）。

    容错（2026-09-18 真丢包演练修复）：DB 故障（丢包/池超时）时字典读取**降级为空列表**
    而非抛异常——读取点位于 serializer 字段绑定（`DictChoiceField.bind → resolve_choices`）
    与请求路径上（URLconf 加载/缓存过期后的首个请求），异常会冒泡为 500；且失败结果不写
    缓存，故障持续期间每个请求都会重试（曾实测每请求 5s 池超时后 500）。降级保证请求可用，
    故障恢复后下一个未缓存请求立即重试。
    """
    data_dict_model = apps.get_model("system", "DataDict")
    cache_key = f"{DICT_CACHE_PREFIX}{code}"

    def _load():
        return [
            {"label": item.label, "label_en": item.label_en, "value": item.value, "color": item.color}
            for item in data_dict_model.objects.filter(
                parent__code=code, parent__is_active=True, is_active=True
            ).order_by("sort", "created_time")
        ]

    rows = cache.get(cache_key)
    if rows is None:
        try:
            rows = _load()
        except Exception:  # noqa: BLE001 字典读取失败降级：不阻断请求，恢复后自动重试
            logger.warning("data dict load failed, degrade to empty: %s", code, exc_info=True)
            return []
        cache.set(cache_key, rows, DICT_CACHE_TIMEOUT)

    use_en = (translation.get_language() or "").startswith("en")
    return [
        {
            "label": (row["label_en"] or row["label"]) if use_en else row["label"],
            "value": row["value"],
            "color": row["color"],
        }
        for row in rows
    ]


def invalid_dict_cache(code=None):
    """失效字典缓存；code 为空时清全部字典缓存（字典类型删除/批量变更场景）。"""
    if code:
        cache.delete(f"{DICT_CACHE_PREFIX}{code}")
        return
    data_dict_model = apps.get_model("system", "DataDict")
    try:
        codes = list(data_dict_model.objects.filter(parent=None).values_list("code", flat=True))
    except Exception:  # noqa: BLE001 信号路径上的 DB 故障不冒泡（缓存随 TTL 自愈）
        logger.warning("invalid_dict_cache skipped due to db error", exc_info=True)
        return
    if codes:
        cache.delete_many([f"{DICT_CACHE_PREFIX}{item}" for item in codes])
