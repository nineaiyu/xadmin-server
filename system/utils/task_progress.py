#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务进度统一更新助手（遗留收口）。

三类异步任务（导出 / 导入 / 报表）的进度写入此前各自为政：导出用局部助手
``_save_progress``、导入走缓存通道（``import_progress``）、报表仅写终态 ``100``。
本模块把「写哪、怎么写」收口为一处（func:`update_progress`）：

- **落库**（导出 / 报表）：更新 ``progress`` + ``stage``（阶段描述）+ ``updated_time``；
- **缓存**（导入运行期）：导入跑在「外层一个大事务 + 每行 savepoint」的原子语义里，
  事务提交前其他连接（下载中心）读不到库内进度（见 ``import_progress`` 模块说明），
  故运行期走缓存、终态（100）落库；
- **协作式取消**：导出 / 报表的中间里程碑统一做一次取消检查（里程碑即安全点，
  终态 100 不检查——避免已完成的导出被翻成取消）；导入的取消检查在其批次循环内
  （回滚语义不同，见 ``_import.py``）。

新增任务类型 = 在 ``_RECORD_MODELS`` 登记「kind → 记录模型」并复用本入口。
"""

from importlib import import_module

from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

KIND_EXPORT = "export"
KIND_IMPORT = "import"
KIND_REPORT = "report"

#: 进度落库模型（延迟解析，避免模块加载期依赖 app registry）
_RECORD_MODELS = {
    KIND_EXPORT: ("system.models.export", "ExportRecord"),
    KIND_IMPORT: ("system.models.import_", "ImportRecord"),
    KIND_REPORT: ("system.models.export", "ExportRecord"),
}

#: 阶段描述最大长度（模型字段同口径）
STAGE_MAX_LENGTH = 64


def normalize_percent(percent) -> int:
    """进度归一（0-100），入参非法（None / 非数字）按 0 处理。"""
    try:
        value = int(percent)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def update_progress(kind: str, record_id, percent, stage: str = "") -> int:
    """更新任务进度（0-100）并返回归一后的百分比。

    :param kind: 任务类型（``export`` / ``import`` / ``report``）
    :param record_id: 记录主键（导出 / 报表为 ExportRecord.pk，导入为 ImportRecord.pk）
    :param percent: 进度百分比（自动归一化）
    :param stage: 阶段描述（可选，仅落库通道写入；超出长度截断）
    """
    percent = normalize_percent(percent)
    if kind not in _RECORD_MODELS:
        logger.warning("unknown task progress kind: %s", kind)
        return percent
    if kind == KIND_IMPORT and percent < 100:
        from system.utils.import_progress import set_import_progress

        set_import_progress(record_id, percent)
        return percent
    if percent < 100 and kind in (KIND_EXPORT, KIND_REPORT):
        from system.utils.task_center import ensure_not_cancelled

        ensure_not_cancelled(record_id)
    module_name, model_name = _RECORD_MODELS[kind]
    model = getattr(import_module(module_name), model_name)
    values = {"progress": percent, "updated_time": timezone.now()}
    if stage:
        values["stage"] = str(stage)[:STAGE_MAX_LENGTH]
    model.objects.filter(pk=record_id).update(**values)
    return percent
