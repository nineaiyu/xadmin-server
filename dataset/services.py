#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据分析与动态表单对外契约门面（``dataset.services``）。

跨 app 消费方（AI 助手的 NL 查数 / 内置动作、审批流的 dform 回写等）经此
使用数据集执行面与动态表单校验面，避免直接依赖内部 utils/models 结构。
"""

from dataset.utils.dataset import (
    ALLOWED_METRICS,
    ALLOWED_OPS,
    ROW_LIMIT_CAP,
    aggregate_dataset,
    available_fields,
    build_queryset,
    execute_dataset,
    filter_layout_for_user,
    get_whitelisted_model,
)
from dataset.utils.dform import validate_submission_data

__all__ = [
    "ALLOWED_METRICS",
    "ALLOWED_OPS",
    "ROW_LIMIT_CAP",
    "aggregate_dataset",
    "available_fields",
    "build_queryset",
    "execute_dataset",
    "filter_layout_for_user",
    "get_whitelisted_model",
    "validate_submission_data",
]

# 惰性再导出表：模型类（供跨 app 惰性消费，属性访问时才加载）
_LAZY_EXPORTS = {
    "Dataset": "dataset.models.dataset",
    "Dashboard": "dataset.models.dataset",
    "Screen": "dataset.models.dataset",
    "Report": "dataset.models.dataset",
    "DynamicForm": "dataset.models.dform",
    "DynamicFormSubmission": "dataset.models.dform",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
