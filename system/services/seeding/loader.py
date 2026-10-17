#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""装载准备：把计划交给装配逻辑，返回可直接交给 ``loaddata`` 的文件清单。

本模块不执行 ``loaddata``（由管理命令承接，便于测试替换），只负责「把哪些文件按什么顺序装」。
"""

from pathlib import Path
from typing import Any

from django.apps import apps as django_apps
from django.db import DEFAULT_DB_ALIAS

from .rows import build_fixtures
from .types import LoadReport, SeedPlan


def prepare(
    plan: SeedPlan,
    target_dir: Path | str,
    *,
    module_filter: Any = None,
    using: Any = DEFAULT_DB_ALIAS,
) -> LoadReport:
    """装配待导入种子：模块裁剪 → 冲突预检 → （必要时）落临时目录。"""

    specs = [*plan.all_specs(), *plan.samples]
    labels, notes, trimmed = build_fixtures(specs, target_dir, module_filter=module_filter, using=using)
    return LoadReport(
        fixture_labels=[Path(label) for label in labels],
        notes=list(notes),
        trimmed=trimmed,
    )


def model_classes(plan: SeedPlan) -> list[Any]:
    """计划覆盖的模型类（去重保序），用于导入后的时间戳回填。"""

    models = []
    for label in plan.model_labels():
        model = django_apps.get_model(label)
        if model is not None:
            models.append(model)
    return models
