#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
system app 对外服务契约层。

其他 app 需要使用 system 的业务能力时，只允许从本模块导入，
禁止直接 import system.views / system.serializers / system.models 等内部实现，
避免 app 间横向依赖扩散。

模型再导出说明：跨 app 关联（isinstance 判断、类型标注、related field
queryset、serializer Meta.model 等场景）统一经由本模块引用，禁止绕过
契约层直接 import system.models。

惰性导出说明：模型 / 序列化器 / 信号经 __getattr__ 按需加载并缓存到模块
globals，``from system.services import Menu`` 这类 from-import 仍然可用；
本模块自身保持零重导入（顶层不 import system.models 等），供 common.core
等底层模块安全顶层引用，避免循环导入。login_success 位于视图层
（import 链很重：login → verify_code → common.tasks），同理按需加载。
"""

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "UserInfo",  # noqa: F822
    "UserLoginLog",  # noqa: F822
    "UploadFile",  # noqa: F822
    "SystemConfig",  # noqa: F822
    "UserPersonalConfig",  # noqa: F822
    "OperationLog",  # noqa: F822
    "PersonalAccessToken",  # noqa: F822
    "Menu",  # noqa: F822
    "FieldPermission",  # noqa: F822
    "DataPermission",  # noqa: F822
    "ModeTypeAbstract",  # noqa: F822
    "DeptInfo",  # noqa: F822
    "ModelLabelField",  # noqa: F822
    "UserRole",  # noqa: F822
    # 序列化器契约
    "UserInfoSerializer",  # noqa: F822
    # 信号契约
    "invalid_user_cache_signal",  # noqa: F822
    # 服务函数
    "get_superusers",
    "get_active_superuser_queryset",
    "get_users_by_pks",
    "get_users_by_perm",
    "get_users_by_perms",
    "get_active_user_pk_by_username",
    "serialize_user_info",
    "register_user_session",
    "websocket_session_logout",
    # 契约委托函数（观察项收口：common 侧函数级业务 import 的模块级替代）
    "emit_webhook_event",
    "maybe_alert_sensitive_operation",
    "publish_api_quota_warning",
    "apply_grant_fields",
    "apply_grant_row_scope",
    "application_of_request",
    "enforce_application_grant",
    "resolve_request_menu_pk",
    "apply_mask",
    "get_mask_rules",
    "record_original_channel_access",
    "ensure_impact_confirmed",
    "impact_for_many",
    "guarded_models",
    "sync_model_field",
    "scan_permission_gaps",
    "api_action_specs",
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "UserInfo": "system.models",
    "UserLoginLog": "system.models",
    "UploadFile": "system.models",
    "SystemConfig": "system.models",
    "UserPersonalConfig": "system.models",
    "OperationLog": "system.models",
    "PersonalAccessToken": "system.models",
    "Menu": "system.models",
    "FieldPermission": "system.models",
    "DataPermission": "system.models",
    "ModeTypeAbstract": "system.models",
    "DeptInfo": "system.models",
    "ModelLabelField": "system.models",
    "UserRole": "system.models.role",
    "UserInfoSerializer": "system.serializers.userinfo",
    # 周期任务/审批序列化器的展示增强字段（DisplayRelatedField）与打标序列化混入
    # （TaggedObjectSerializerMixin）：审批域拆分后经本契约门面消费（模块级 import
    # 不违反跨 app 门禁的 services 契约通道）
    "DisplayRelatedField": "system.serializers.task",
    "TaggedObjectSerializerMixin": "system.serializers.tag",
    "invalid_user_cache_signal": "system.signal",
    # AI 动作声明注册表（dict 常量；ai_meta / MCP tools 共用的单一来源）
    "API_ACTION_SPECS": "system.utils.ai_api_registry",
}


def __getattr__(name):
    if name == "login_success":
        from system.views.auth.login import login_success

        globals()[name] = login_success
        return login_success

    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_superusers():
    """超级管理员 queryset（不过滤启用状态，保持历史行为）。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True)


def get_active_superuser_queryset():
    """在用的超级管理员 queryset。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True, is_active=True)


def get_users_by_pks(pks):
    """按主键批量取用户。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(id__in=pks).all()


def get_users_by_perm(perm):
    """按单个权限码反查在用用户（get_users_by_perms 单码薄封装）。"""
    return get_users_by_perms([perm])


def get_users_by_perms(perms):
    """按权限码清单反查在用用户（任一命中，去重）。

    权限码（"动作:组件名"，如 approve:SystemApprovalRequest）挂 PERMISSION 类型
    菜单的 name，经 角色↔菜单 授权间接授予用户（即"按权限反查"）。
    软删除角色/菜单显式排除，不依赖关联查询的管理器行为。

    通用职能推导工具：审批人解析（system.utils.approval.get_approver_queryset）
    与通知接收人解析共用，替代逐处复制"角色清单成员查询"。
    """
    from system.models import Menu, UserInfo

    if not perms:
        return UserInfo.objects.none()
    return UserInfo.objects.filter(
        is_active=True,
        roles__is_active=True,
        roles__deleted_at__isnull=True,
        roles__menu__is_active=True,
        roles__menu__deleted_at__isnull=True,
        roles__menu__menu_type=Menu.MenuChoices.PERMISSION,
        roles__menu__name__in=list(perms),
    ).distinct()


def get_active_user_pk_by_username(username):
    """按用户名取在用用户主键，不存在返回 None。"""
    from system.models import UserInfo

    return UserInfo.objects.filter(username=username, is_active=True).values_list("pk", flat=True).first()


def serialize_user_info(user) -> dict:
    """按对外契约序列化用户信息（供 WebSocket 等非 DRF 场景使用）。"""
    from system.serializers.userinfo import UserInfoSerializer

    return UserInfoSerializer(instance=user).data


def register_user_session(request, user, login_type, channel_name=""):
    """登录/WS 接入时登记会话（system.utils.session 契约导出，供 message app 使用）。"""
    from system.utils.session import register_user_session as _register

    return _register(request, user, login_type, channel_name=channel_name)


def websocket_session_logout(channel_name):
    """WS 优雅断开时按 channel 置会话离线（异常残留由保留期清理任务兜底）。"""
    from django.utils import timezone

    from system.models import UserSession

    UserSession.objects.filter(channel_name=channel_name, status=UserSession.Status.ONLINE).update(
        status=UserSession.Status.OFFLINE, last_active=timezone.now()
    )


# ---------------------------------------------------------------------------
# 契约委托函数（2026-09-26 观察项收口）：common 侧的函数级业务 import 全部
# 收敛为本模块的模块级契约缝。委托体保持调用期惰性加载——与原函数级 import
# 等价（循环依赖 / 迁移期降级语义不变），但缝隙在门禁 CONTRACT_SEAMS 显式
# 登记可审计。新增委托时同步登记 CONTRACT_SEAMS 并更新 __all__。
# ---------------------------------------------------------------------------


def emit_webhook_event(event, payload):
    """出站 Webhook 事件投递（system.utils.webhook 契约导出）。"""
    from system.utils.webhook import emit_webhook_event as _emit

    return _emit(event, payload)


def maybe_alert_sensitive_operation(info):
    """敏感操作告警分流（system.notifications 契约导出）。"""
    from system.notifications import maybe_alert_sensitive_operation as _alert

    return _alert(info)


def publish_api_quota_warning(info):
    """API 配额告警：系统消息 + 出站 Webhook（system.notifications 契约导出）。"""
    from system.notifications import ApiQuotaWarningMessage

    ApiQuotaWarningMessage(info).publish(is_async=True)


def apply_grant_fields(request, model_label, allowed):
    """应用凭证字段授权：可见字段收敛（system.utils.api_grant 契约导出）。"""
    from system.utils.api_grant import apply_grant_fields as _apply

    return _apply(request, model_label, allowed)


def apply_grant_row_scope(request, queryset):
    """应用凭证行级授权：queryset 收敛（system.utils.api_grant 契约导出）。"""
    from system.utils.api_grant import apply_grant_row_scope as _apply

    return _apply(request, queryset)


def application_of_request(request):
    """请求关联的应用凭证（system.utils.api_grant 契约导出）。"""
    from system.utils.api_grant import application_of_request as _resolve

    return _resolve(request)


def enforce_application_grant(request, view):
    """应用凭证权限点校验（system.utils.api_grant 契约导出）。"""
    from system.utils.api_grant import enforce_application_grant as _enforce

    return _enforce(request, view)


def resolve_request_menu_pk(request):
    """按请求路径解析应用凭证菜单（system.utils.api_grant 契约导出）。"""
    from system.utils.api_grant import resolve_request_menu_pk as _resolve

    return _resolve(request)


def apply_mask(value, rule):
    """按掩码规则脱敏单值（system.utils.mask 契约导出）。"""
    from system.utils.mask import apply_mask as _apply

    return _apply(value, rule)


def get_mask_rules(model_label):
    """取模型掩码规则（system.utils.mask 契约导出）。"""
    from system.utils.mask import get_mask_rules as _get

    return _get(model_label)


def record_original_channel_access(request, user, model_label=None):
    """掩码通道明文访问审计（system.utils.mask 契约导出）。"""
    from system.utils.mask import record_original_channel_access as _record

    return _record(request, user, model_label)


def ensure_impact_confirmed(view, request, instances=None, queryset=None):
    """删除影响面确认校验（system.utils.impact 契约导出）。"""
    from system.utils.impact import ensure_impact_confirmed as _ensure

    return _ensure(view, request, instances=instances, queryset=queryset)


def impact_for_many(objects) -> dict:
    """批量影响面预览（system.utils.impact 契约导出）。"""
    from system.utils.impact import impact_for_many as _impact

    return _impact(objects)


def guarded_models() -> set:
    """登记影响面保护的模型清单（system.utils.impact 契约导出）。"""
    from system.utils.impact import guarded_models as _guarded

    return _guarded()


def sync_model_field():
    """模型字段权限树同步（system.utils.modelfield 契约导出）。"""
    from system.utils.modelfield import sync_model_field as _sync

    return _sync()


def scan_permission_gaps():
    """权限点缺口扫描（system.utils.permission_sync 契约导出）。"""
    from system.utils.permission_sync import scan_permission_gaps as _scan

    return _scan()


def api_action_specs() -> dict:
    """AI 动作声明注册表（system.utils.ai_api_registry 契约导出，dict 常量）。"""
    from system.utils.ai_api_registry import API_ACTION_SPECS

    return API_ACTION_SPECS
