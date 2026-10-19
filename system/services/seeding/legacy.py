#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""平台层种子的历史装载顺序（兼容基线）。

拆分前所有种子按「每模型一文件」平铺在 ``loadjson/``，装载顺序由这份手工拓扑序决定
（``menumeta`` 先于 ``menu``、字段树先于字段白名单、流程先于节点与版本、数据集先于看板）。

拆分后 app 层顺序由各自的 ``manifest.json`` 决定；平台层仍以本序列为主干，
保证「未迁移的文件装载顺序与拆分前完全一致」。新增平台层文件应直接写进 ``loadjson/manifest.json``
（会追加在本序列之后），不再扩这张表。
"""

from typing import Any

from django.apps import apps as django_apps

# 模型 label 而非类：避免 system 服务层直接 import 其它 app 的模型模块（跨应用门禁）
LEGACY_MODEL_LABELS = (
    "system.menumeta",
    "system.menu",
    "system.systemconfig",
    "system.datadict",
    "system.datapermission",
    "identity.userrole",
    "system.fieldpermission",
    "system.modellabelfield",
    "identity.deptinfo",
    "settings.setting",
    # 数据分析内置示例：固定 pk 相互引用，必须按依赖顺序加载
    "dataset.dataset",
    "dataset.dashboard",
    "dataset.screen",
    "dataset.report",
    # 脱敏 / 审批 / 表单采集内置示例：实例类模型（审批单 / 节点任务 / 请求）禁止入册
    "audit.datamaskrule",
    "identity.loginaccesspolicy",
    "approval.approvalflow",
    "approval.approvalflownode",
    "approval.approvalflowversion",
    "dataset.dynamicform",
    "dataset.dynamicformsubmission",
)


def legacy_models() -> tuple[Any, ...]:
    """按历史顺序返回已注册的种子模型。

    未注册的 app 自动跳过：种子面随 ``INSTALLED_APPS`` 收窄（拆分前在 import 期直接崩溃）。
    """

    models = []
    for label in LEGACY_MODEL_LABELS:
        model = django_apps.get_model(label)
        if model is not None:
            models.append(model)
    return tuple(models)
