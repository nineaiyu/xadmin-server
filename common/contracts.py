#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""common（框架层）→ 业务 app 的唯一显式契约出口（ADR-079）。

方向规则：common 内禁止直接 import 业务 app 的 services / models，业务能力
统一经本模块消费。契约接口 = 「声明式白名单 + Protocol 消费面」，全部写在
框架层（common）一侧：

1. ``_CONTRACT_PROVIDERS``：契约名 → (提供方模块, 原因)。清单即接口声明——
   名字之外的能力对 common 不可达（``__getattr__`` 直接 AttributeError）；
2. ``__all__`` 由白名单派生（单一事实源，不漂移）；
3. ``SystemConfigContract`` / ``MenuContract``：两处属性访问式消费面（凭据
   巡检 / 模块裁剪）的 Protocol——「common 需要业务 app 提供什么」以接口
   形式显式落档，供守护测试与二开提供方对照实现。

解析机制与 ``system.services`` 的惰性导出同构（PEP 562 ``__getattr__`` +
模块 globals 缓存）：from-import 用法与拆分前完全一致，解析发生在消费方
import 期（或调用期，对属性访问式消费点），模型加载时机不前移不后移；
解析异常不在此吞掉，迁移期降级语义仍由调用方既有 except 分支承担。

门禁（``scripts/check_cross_app_imports.py``）保证：common 内除本模块外
出现任何业务 app 模块级 import 即违例；本模块的缝在 CONTRACT_SEAMS 登记
（双向漂移校验）。

注入制升级留触发（ADR-079 D4）：二开生态 / common 独立包化时，本模块升级
为可注入提供方（AppConfig.ready 注册实现），common 其余代码零改动。
"""

from typing import Any, Protocol

#: 契约名 → (提供方模块, 原因)。新增契约：此处登记 + 门禁 CONTRACT_SEAMS
#: 同步 + tests/unit/common/test_contracts.py 往返守护自动覆盖。
_CONTRACT_PROVIDERS: dict[str, tuple[str, str]] = {
    # --- system.services：模型契约（跨 app 关联 / isinstance / Meta.model 场景） ---
    "SystemConfig": ("system.services", "运行期配置模型（config 缓存基座 + 凭据巡检）"),
    "UserPersonalConfig": ("system.services", "用户个人配置模型（config 缓存基座）"),
    "OperationLog": ("system.services", "审计日志模型（操作日志记录 / 中间件审计）"),
    "PersonalAccessToken": ("system.services", "PAT 模型（审计日志的类型判定）"),
    "Menu": ("system.services", "菜单模型（模块裁剪 / 字段权限 / 生成器种子关联）"),
    "FieldPermission": ("system.services", "字段权限模型（权限层消费）"),
    "DataPermission": ("system.services", "数据权限模型（行级过滤规则加载）"),
    "DeptInfo": ("system.services", "部门模型（数据权限主体与行级过滤）"),
    "UserInfo": ("system.services", "用户模型（数据权限主体与行级过滤）"),
    "ModelLabelField": ("system.services", "模型字段注册模型（数据权限常量 / 生成器）"),
    "ModeTypeAbstract": ("system.services", "数据权限模式常量（数据权限编译器）"),
    "UserRole": ("system.services", "角色模型（生成器种子关联）"),
    # --- system.services：契约委托函数（services 侧已惰性委托，import 零模型加载） ---
    "emit_webhook_event": ("system.services", "出站 Webhook 事件投递（告警发布共用）"),
    "get_active_superuser_queryset": ("system.services", "告警收件人解析（在用超管 queryset）"),
    "publish_api_quota_warning": ("system.services", "API 配额告警（系统消息 + Webhook）"),
    "maybe_alert_sensitive_operation": ("system.services", "敏感操作告警分流（审计中间件）"),
    "apply_grant_fields": ("system.services", "应用凭证字段授权（序列化器可见字段收敛）"),
    "apply_grant_row_scope": ("system.services", "应用凭证行级授权（queryset 收敛）"),
    "application_of_request": ("system.services", "请求关联的应用凭证解析"),
    "enforce_application_grant": ("system.services", "应用凭证权限点校验"),
    "resolve_request_menu_pk": ("system.services", "按请求路径解析应用凭证菜单"),
    "apply_mask": ("system.services", "字段掩码：单值脱敏（序列化器应用）"),
    "get_mask_rules": ("system.services", "字段掩码：规则加载"),
    "record_original_channel_access": ("system.services", "字段掩码：原文通道访问审计"),
    "ensure_impact_confirmed": ("system.services", "删除影响面确认校验（modelset 基座）"),
    "impact_for_many": ("system.services", "批量影响面预览（modelset 基座）"),
    "guarded_models": ("system.services", "影响面保护模型清单（modelset 基座）"),
    "sync_model_field": ("system.services", "模型字段权限树同步（生成器回填）"),
    "scan_permission_gaps": ("system.services", "权限点缺口扫描（启动自检）"),
    # --- notifications.services：消息渠道生产面（common 是渠道的通用生产者） ---
    "BACKEND": ("notifications.services", "消息渠道后端常量（渠道注册表键）"),
    "SystemMessage": ("notifications.services", "系统消息实体（运维/备份/任务失败告警载体）"),
    "SystemMsgSubscription": ("notifications.services", "系统消息订阅实体（告警订阅装配）"),
    "UserMessage": ("notifications.services", "用户站内信实体（渠道投递）"),
    "register_message": ("notifications.services", "消息渠道注册（渠道生产者登记）"),
    # --- 其它业务 app 契约 ---
    "process_approval": ("approval.services", "审批流拦截入口（通用装饰器消费）"),
    "API_ACTION_SPECS": ("ai.services", "AI 动作声明注册表（OpenAPI 元数据派生）"),
    "Setting": ("settings.services", "Setting 模型（启动自检迁移就绪探测）"),
    "LoginBlockUtil": ("settings.services", "文档站登录账号锁定（与主登录链路同计数）"),
    "LoginIpBlockUtil": ("settings.services", "文档站登录 IP 锁定（与主登录链路同计数）"),
}

__all__ = tuple(_CONTRACT_PROVIDERS)


class MenuChoicesContract(Protocol):
    """模块裁剪消费的菜单类型常量面（只读 PERMISSION 一项）。"""

    PERMISSION: str


class MenuContract(Protocol):
    """模块裁剪消费的 Menu 面（``common/core/modules/gate.py``）。

    裁剪需要：全量菜单树的 ``(pk, parent_id, menu_type, name, path)`` 行 +
    权限点类型常量。二开提供方实现本协议即可替换模块裁剪的数据源。
    """

    objects: Any  # .values_list("pk", "parent_id", "menu_type", "name", "path")
    MenuChoices: type[MenuChoicesContract]


class SystemConfigContract(Protocol):
    """凭据巡检消费的 SystemConfig 面（``common/core/credentials.py``）。

    巡检需要：按键批取 ``(key, value)`` 行（明文判定 / 加密状态统计）。
    """

    objects: Any  # .filter(key__in=...) → .values_list("key", "value")


def __getattr__(name: str) -> Any:
    """PEP 562 惰性解析：契约名按白名单从提供方模块取值并缓存。

    与 ``system.services.__getattr__`` 同构：解析失败（迁移期模型不可用 /
    未声明名字）原样抛出，不在此降级——降级口径归调用方。
    """
    provider = _CONTRACT_PROVIDERS.get(name)
    if provider is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r} "
            f"(框架层契约面未声明该名字——业务能力须经 common/contracts.py 声明后消费，见 ADR-079)"
        )
    from importlib import import_module

    value = getattr(import_module(provider[0]), name)
    globals()[name] = value  # 首次访问后缓存为模块属性
    return value
