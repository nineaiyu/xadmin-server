#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批终态回写注册表：biz_type → 业务同步器（声明式收集，无核心分发分支）。

流程实例终态经 ``approval.signal.approval_instance_finished`` 广播后，
``approval/signal_handler.py`` 按 ``biz_type`` 在此解析业务同步器并回写业务单。

接入方式（二开零核心文件改动）——业务 app 在自身 ``config.py`` 声明::

    APPROVAL_BIZ_SYNCERS = {
        "my_biz": "myapp.services.sync_my_biz_instance",
    }

先例与 ``common/celery/routing.py`` 的 ``TASK_ROUTES`` 声明式合并同构：
框架内置表兜底、应用声明按 biz_type 合并，导入路径在分发时才惰性 import
——业务 app 未装载（模块裁剪）时自然不参与收集，不产生任何导入开销。

同步器契约：
- 签名 ``sync(instance, status, reason="")``；按 ``instance.biz_type/biz_id``
  自行认领（biz_type 不匹配直接返回），自身保证幂等；
- 同步器异常由接收器兜底记日志，不得反向阻断审批状态机。
"""

from functools import lru_cache
from importlib import import_module

from django.apps import apps

from common.utils import get_logger

logger = get_logger(__name__)

# approval 域内置业务：请假单（业务模型与同步器都在本 app 内）
BUILTIN_BIZ_SYNCERS = {
    "leave": "approval.utils.leave.sync_leave_instance",
}


@lru_cache(maxsize=1)
def _app_biz_syncers() -> dict:
    """收集各应用 config.py 的 APPROVAL_BIZ_SYNCERS 声明（biz_type → 导入路径）。

    仅在首次分发时执行（django.setup 已完成）；结果进程级缓存——改声明需重启，
    与 TASK_ROUTES / URLPATTERNS 的 config.py 语义同口径。
    """
    collected: dict = {}
    for app_config in apps.get_app_configs():
        try:
            module = import_module(f"{app_config.name}.config")
        except ModuleNotFoundError as e:
            # 仅吞掉「无 config.py」；config.py 内部 import 失败必须暴露
            if e.name not in (f"{app_config.name}.config", app_config.name):
                raise
            continue
        declared = getattr(module, "APPROVAL_BIZ_SYNCERS", None)
        if not declared:
            continue
        for biz_type, syncer_path in declared.items():
            collected[biz_type] = syncer_path
        logger.info(f"auto register {app_config.name} approval biz syncers: {sorted(declared)}")
    return collected


def get_biz_syncer(biz_type: str):
    """按 biz_type 解析业务同步器函数；应用声明优先，内置表兜底，未注册返回 None。"""
    path = _app_biz_syncers().get(biz_type) or BUILTIN_BIZ_SYNCERS.get(biz_type)
    if not path:
        return None
    module_path, _, attr = path.rpartition(".")
    return getattr(import_module(module_path), attr)


def registered_biz_types() -> tuple:
    """已注册的全部 biz_type（内置 + 应用声明），供诊断/校验类消费方枚举。"""
    return tuple({**BUILTIN_BIZ_SYNCERS, **_app_biz_syncers()})
