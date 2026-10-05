#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""system app（platform 域）对外服务契约层。

其他 app 需要使用 system 的业务能力时，只允许从本模块导入，
禁止直接 import system.views / system.serializers / system.models 等内部实现，
避免 app 间横向依赖扩散。identity / file / audit / task 四域已各自独立成 app，
契约面分别在各 app 的 services（identity.services / file.services / audit.services /
task.services）；本模块只承载留存 platform 域（配置 / 菜单 / 权限 / 字典 / 标签 /
模型字段）与其余未拆域的过渡契约。

模型再导出说明：跨 app 关联（isinstance 判断、类型标注、related field
queryset、serializer Meta.model 等场景）统一经由本模块引用，禁止绕过
契约层直接 import system.models。

惰性导出说明：模型 / 序列化器经 __getattr__ 按需加载并缓存到模块
globals，``from system.services import Menu`` 这类 from-import 仍然可用；
本模块自身保持零重导入（顶层不 import system.models 等），供 common.core
等底层模块安全顶层引用，避免循环导入。
"""

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "UserLoginLog",  # noqa: F822
    "SystemConfig",  # noqa: F822
    "UserPersonalConfig",  # noqa: F822
    "OperationLog",  # noqa: F822
    "Menu",  # noqa: F822
    "FieldPermission",  # noqa: F822
    "DataPermission",  # noqa: F822
    "ModeTypeAbstract",  # noqa: F822
    "ModelLabelField",  # noqa: F822
    # 序列化器契约
    # 周期任务/审批序列化器的展示增强字段（DisplayRelatedField）与打标序列化混入
    # （TaggedObjectSerializerMixin）：审批域拆分后经本契约门面消费（模块级 import
    # 不违反跨 app 门禁的 services 契约通道）
    "DisplayRelatedField",  # noqa: F822
    "TaggedObjectSerializerMixin",  # noqa: F822
    # 契约委托函数（观察项收口：common 侧函数级业务 import 的模块级替代）
    "emit_webhook_event",
    "maybe_alert_sensitive_operation",
    "apply_mask",
    "get_mask_rules",
    "record_original_channel_access",
    "ensure_impact_confirmed",
    "impact_for_many",
    "guarded_models",
    "sync_model_field",
    "scan_permission_gaps",
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "UserLoginLog": "system.models",
    "SystemConfig": "system.models",
    "UserPersonalConfig": "system.models",
    "OperationLog": "system.models",
    "Menu": "system.models",
    "FieldPermission": "system.models",
    "DataPermission": "system.models",
    "ModeTypeAbstract": "system.models",
    "ModelLabelField": "system.models",
    "DisplayRelatedField": "system.serializers.task",
    "TaggedObjectSerializerMixin": "system.serializers.tag",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# 契约委托函数（2026-09-26 观察项收口）：common 侧的函数级业务 import 全部
# 收敛为本模块的模块级契约缝。委托体保持调用期惰性加载——与原函数级 import
# 等价（循环依赖 / 迁移期降级语义不变），但缝隙在门禁 CONTRACT_SEAMS 显式
# 登记可审计。新增委托时同步登记 CONTRACT_SEAMS 并更新 __all__。
# ---------------------------------------------------------------------------


def emit_webhook_event(event, payload):
    """出站 Webhook 事件投递（system.utils.task.webhook 契约导出）。"""
    from system.utils.task.webhook import emit_webhook_event as _emit

    return _emit(event, payload)


def maybe_alert_sensitive_operation(info):
    """敏感操作告警分流（system.notifications 契约导出）。"""
    from system.notifications import maybe_alert_sensitive_operation as _alert

    return _alert(info)


def apply_mask(value, rule):
    """按掩码规则脱敏单值（system.utils.audit.mask 契约导出）。"""
    from system.utils.audit.mask import apply_mask as _apply

    return _apply(value, rule)


def get_mask_rules(model_label):
    """取模型掩码规则（system.utils.audit.mask 契约导出）。"""
    from system.utils.audit.mask import get_mask_rules as _get

    return _get(model_label)


def record_original_channel_access(request, user, model_label=None):
    """掩码通道明文访问审计（system.utils.audit.mask 契约导出）。"""
    from system.utils.audit.mask import record_original_channel_access as _record

    return _record(request, user, model_label)


def ensure_impact_confirmed(view, request, instances=None, queryset=None):
    """删除影响面确认校验（system.utils.audit.impact 契约导出）。"""
    from system.utils.audit.impact import ensure_impact_confirmed as _ensure

    return _ensure(view, request, instances=instances, queryset=queryset)


def impact_for_many(objects) -> dict:
    """批量影响面预览（system.utils.audit.impact 契约导出）。"""
    from system.utils.audit.impact import impact_for_many as _impact

    return _impact(objects)


def guarded_models() -> set:
    """登记影响面保护的模型清单（system.utils.audit.impact 契约导出）。"""
    from system.utils.audit.impact import guarded_models as _guarded

    return _guarded()


def sync_model_field():
    """模型字段权限树同步（system.utils.platform.modelfield 契约导出）。"""
    from system.utils.platform.modelfield import sync_model_field as _sync

    return _sync()


def scan_permission_gaps():
    """权限点缺口扫描（system.utils.platform.permission_sync 契约导出）。"""
    from system.utils.platform.permission_sync import scan_permission_gaps as _scan

    return _scan()


def get_dict_items(code):
    """数据字典条目下发（system.utils.platform.dict 契约导出）。"""
    from system.utils.platform.dict import get_dict_items as _get

    return _get(code)
