#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task app（任务域）对外服务契约层。

其他 app（含框架层经 common.contracts）需要使用 task 域业务能力时，只允许从
本模块导入，禁止直接 import task.models / task.serializers / task.utils 等
内部实现（与 identity.services / file.services / audit.services / system.services
同一口径）。

覆盖面：
- 模型契约：TaskExecution / ExportRecord / ImportRecord / ImportTemplate /
  WebhookSubscription / WebhookDelivery（执行历史、下载中心、监控面板、
  演示种子、数据权限表树的跨域消费）；
- 序列化器契约：DisplayRelatedField（展示增强主键字段，platform 序列化器消费）；
- Webhook 契约：emit_webhook_event / deliver_webhook / EVENT_CATALOG /
  URL 与签名工具（身份域登录告警、审批事件、开放接口凭据回执共用）；
- 进度契约：update_progress / KIND_REPORT（dataset 报表导出进度上报）；
- 导入导出实现：run_async_export / run_async_import（celery 任务壳的委托体），
  及导出产物协议 EXPORT_MIME_TYPES / mime_type_for / persist_export_artifact
  （定时报表链与视图重放链共用的统一导出服务面）；
- 清理契约：clean_task_executions / clean_export_records / clean_import_records /
  auto_clean_black_token（周期任务壳的委托体）。

惰性导出说明：经 PEP 562 ``__getattr__`` 按需加载并缓存到模块 globals，
``from task.services import TaskExecution`` 这类 from-import 仍然可用；本模块
自身保持零重导入，避免循环导入。
"""

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "TaskExecution",  # noqa: F822
    "ExportRecord",  # noqa: F822
    "ImportRecord",  # noqa: F822
    "ImportTemplate",  # noqa: F822
    "WebhookSubscription",  # noqa: F822
    "WebhookDelivery",  # noqa: F822
    "CeleryTaskRecordModel",  # noqa: F822
    # 序列化器契约
    "DisplayRelatedField",  # noqa: F822
    # Webhook 契约
    "emit_webhook_event",  # noqa: F822
    "deliver_webhook",  # noqa: F822
    "EVENT_CATALOG",  # noqa: F822
    "decrypt_secret",  # noqa: F822
    "encrypt_secret",  # noqa: F822
    "sign_payload",  # noqa: F822
    "outbound_allowed_hosts",  # noqa: F822
    "validate_url",  # noqa: F822
    # 进度契约
    "update_progress",  # noqa: F822
    "KIND_REPORT",  # noqa: F822
    # 导入导出实现
    "run_async_export",  # noqa: F822
    "run_async_import",  # noqa: F822
    "EXPORT_MIME_TYPES",  # noqa: F822
    "mime_type_for",  # noqa: F822
    "persist_export_artifact",  # noqa: F822
    # 清理契约
    "clean_task_executions",  # noqa: F822
    "clean_export_records",  # noqa: F822
    "clean_import_records",  # noqa: F822
    "auto_clean_black_token",  # noqa: F822
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "TaskExecution": "task.models",
    "ExportRecord": "task.models",
    "ImportRecord": "task.models",
    "ImportTemplate": "task.models",
    "WebhookSubscription": "task.models",
    "WebhookDelivery": "task.models",
    "CeleryTaskRecordModel": "task.models",
    "DisplayRelatedField": "task.serializers.task",
    "emit_webhook_event": "task.utils.webhook",
    "deliver_webhook": "task.webhook_tasks",
    "EVENT_CATALOG": "task.utils.webhook",
    "decrypt_secret": "task.utils.webhook",
    "encrypt_secret": "task.utils.webhook",
    "sign_payload": "task.utils.webhook",
    "outbound_allowed_hosts": "task.utils.webhook",
    "validate_url": "task.utils.webhook",
    "update_progress": "task.utils.task_progress",
    "KIND_REPORT": "task.utils.task_progress",
    "run_async_export": "task.services._export",
    "run_async_import": "task.services._import",
    "EXPORT_MIME_TYPES": "task.services._export",
    "mime_type_for": "task.services._export",
    "persist_export_artifact": "task.services._export",
    "clean_task_executions": "task.services.cleanup",
    "clean_export_records": "task.services.cleanup",
    "clean_import_records": "task.services.cleanup",
    "auto_clean_black_token": "task.utils.ctasks",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
