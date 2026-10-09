#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit app（审计域）对外服务契约层。

其他 app（含框架层经 common.contracts）需要使用 audit 域业务能力时，只允许从
本模块导入，禁止直接 import audit.models / audit.serializers / audit.utils 等
内部实现（与 identity.services / file.services / system.services 同一口径）。

覆盖面：
- 模型契约：OperationLog / UserLoginLog / DataMaskRule（中间件审计、身份域
  登录留痕、AI 脱敏的模型消费）；
- 掩码契约：apply_mask / get_mask_rules / record_original_channel_access；
- 影响面契约：ensure_impact_confirmed / impact_for_many / guarded_models；
- 告警与清理：maybe_alert_sensitive_operation、auto_clean_operation_log。

惰性导出说明：经 PEP 562 ``__getattr__`` 按需加载并缓存到模块 globals，
``from audit.services import OperationLog`` 这类 from-import 仍然可用；本模块
自身保持零重导入，避免循环导入。
"""

from typing import Any

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "OperationLog",  # noqa: F822
    "UserLoginLog",  # noqa: F822
    "DataMaskRule",  # noqa: F822
    # 序列化器契约（面板聚合等跨域消费）
    "LoginLogSerializer",  # noqa: F822
    # 掩码契约（common.core.mask 消费）
    "apply_mask",  # noqa: F822
    "get_mask_rules",  # noqa: F822
    "record_original_channel_access",  # noqa: F822
    "invalid_mask_cache",  # noqa: F822
    # 影响面契约（common.core.modelset 消费）
    "ensure_impact_confirmed",  # noqa: F822
    "impact_for_many",  # noqa: F822
    "guarded_models",  # noqa: F822
    # 告警与清理
    "maybe_alert_sensitive_operation",  # noqa: F822
    "SENSITIVE_ALERT_THROTTLE_SECONDS",  # noqa: F822
    "auto_clean_operation_log",
    "archive_expired",  # noqa: F822
    "prune_archived",  # noqa: F822
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "OperationLog": "audit.models",
    "UserLoginLog": "audit.models",
    "DataMaskRule": "audit.models",
    "LoginLogSerializer": "audit.serializers.log",
    "apply_mask": "audit.utils.mask",
    "get_mask_rules": "audit.utils.mask",
    "record_original_channel_access": "audit.utils.mask",
    "invalid_mask_cache": "audit.utils.mask",
    "ensure_impact_confirmed": "audit.utils.impact",
    "impact_for_many": "audit.utils.impact",
    "guarded_models": "audit.utils.impact",
    "maybe_alert_sensitive_operation": "audit.notifications_alert",
    "SENSITIVE_ALERT_THROTTLE_SECONDS": "audit.notifications_alert",
    "auto_clean_operation_log": "audit.services.cleanup",
    "archive_expired": "audit.utils.log_archive",
    "prune_archived": "audit.utils.log_archive",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
