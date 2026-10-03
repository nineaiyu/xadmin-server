# -*- coding: utf-8 -*-
"""表名归域守护（TG-3 / ADR-080）。

ADR-057 把 approval / ai / dataset 从 system 拆出时，物理表名以显式
``Meta.db_table = "system_*"`` 冻结；ADR-080 解除冻结（表名回落 Django 默认
``<app>_<模型名小写>``）。本守护防两类回潮：

- **跨域前缀**：非 system app 的模型再挂 ``system_`` 表名（拆分冻结旧形态复辟）；
- **归域漂移**：拆分 app 的表名偏离 Django 默认命名（显式 db_table 再次出现）。

新模型如确需自定义表名，须先 ADR 并在 ``TABLE_NAME_OVERRIDES`` 登记例外。
"""

from django.apps import apps
from django.conf import settings

#: 允许偏离「app 域内默认命名」的显式登记（model label → 期望 db_table）；空 = 无例外。
TABLE_NAME_OVERRIDES = {}

#: 已按域拆分的业务 app（ADR-057 批次 2/3/4）
SPLIT_APPS = ("ai", "approval", "dataset")


def _base_table(meta) -> str:
    """剥离 DB_PREFIX（前缀部署在 class_prepared 期统一叠加，比对按剥离后基名）。"""
    prefix = settings.DB_PREFIX if isinstance(settings.DB_PREFIX, str) else ""
    return meta.db_table.removeprefix(prefix)


def _split_app_models():
    return [model for model in apps.get_models() if model._meta.app_label in SPLIT_APPS]


def test_no_split_app_model_carries_system_prefix():
    violations = [
        f"{model._meta.label} -> {model._meta.db_table}"
        for model in _split_app_models()
        if model._meta.db_table.startswith("system_")
    ]
    assert violations == [], f"拆分 app 模型仍挂 system_ 冻结表名（TG-3 回潮）：{violations}"


def test_split_app_tables_follow_default_domain_naming():
    violations = []
    for model in _split_app_models():
        meta = model._meta
        expected = TABLE_NAME_OVERRIDES.get(meta.label, f"{meta.app_label}_{meta.model_name}")
        actual = _base_table(meta)
        if actual != expected:
            violations.append(f"{meta.label} -> {actual}（期望 {expected}）")
    assert violations == [], f"表名偏离归域默认命名且未登记例外：{violations}"


def test_no_other_app_borrows_system_prefix():
    """system 之外的任何 app（含未来新增）不得新建 system_* 表。"""
    violations = [
        f"{model._meta.label} -> {model._meta.db_table}"
        for model in apps.get_models()
        if model._meta.app_label != "system" and model._meta.db_table.startswith("system_")
    ]
    assert violations == [], f"非 system app 模型挂 system_ 表名：{violations}"
