#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块注册表：后台覆盖配置的读取与写入。

后台覆盖是模块装配的 L1 层（L0 = config.yml / 环境变量）：管理页写入一行，
模块解析优先读它，整体替换部署基线。

读取刻意**不在 app 初始化期**发生（`AppConfig.ready()` 只校验部署基线）：Django
不鼓励在 app 初始化期访问数据库——此时建立的连接指向尚未创建的测试库，会破坏
测试库创建；模块解析改为首次实际使用时读取（见 registry.resolve_modules）。
"""

from dataclasses import dataclass

from django.apps import apps

from common.utils import get_logger

logger = get_logger(__name__)

MODEL_LABEL = "system.ModuleOverride"
OVERRIDE_KEY = "module"


@dataclass(frozen=True)
class ModuleOverrideData:
    """后台覆盖行（None 语义见 load_override）。"""

    preset: str
    enable: tuple
    disable: tuple
    updated_time: object = None


def _model():
    return apps.get_model(MODEL_LABEL)


def load_override():
    """读取后台覆盖行；无行或读取异常（表未建 / 数据库不可达）一律返回 None。"""

    try:
        row = _model().objects.filter(key=OVERRIDE_KEY).first()
    except Exception as exc:  # noqa: BLE001 读不到覆盖行时回退部署基线，不阻断启动
        logger.warning("module override read failed, fallback to deployment config: %s", exc)
        return None
    if row is None:
        return None
    return ModuleOverrideData(
        preset=row.preset,
        enable=tuple(row.enable or ()),
        disable=tuple(row.disable or ()),
        updated_time=row.updated_time,
    )


def save_override(preset, enable=(), disable=()):
    """写入（或更新）覆盖行；异常向上抛出，由调用方转成可读错误。"""

    row, _created = _model().objects.update_or_create(
        key=OVERRIDE_KEY,
        defaults={"preset": preset, "enable": list(enable), "disable": list(disable)},
    )
    return row


def clear_override() -> int:
    """删除覆盖行（恢复为部署基线）；返回删除行数。"""

    return _model().objects.filter(key=OVERRIDE_KEY).delete()[0]
