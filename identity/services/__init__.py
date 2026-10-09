#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity app 对外服务契约层。

其他 app 需要使用 identity 的业务能力时，只允许从本模块导入，
禁止直接 import identity.views / identity.serializers / identity.models 等内部实现，
避免 app 间横向依赖扩散。业务逻辑实现在本包的服务子模块
（``identity.services.auth_login`` / ``open_oauth`` / ``token_issue``），
本模块只做契约面：跨 app 消费方从这里 import，系统内部视图直接消费子模块。

模型再导出说明：跨 app 关联（isinstance 判断、类型标注、related field
queryset、serializer Meta.model 等场景）统一经由本模块引用，禁止绕过
契约层直接 import identity.models。

惰性导出说明：模型 / 序列化器 / 信号经 __getattr__ 按需加载并缓存到模块
globals，``from identity.services import UserInfo`` 这类 from-import 仍然可用；
本模块自身保持零重导入（顶层不 import identity.models 等），供 common.core
等底层模块安全顶层引用，避免循环导入。login_success（登录成功处置，位于
服务子模块 auth_login，import 链很重：login → verify_code → common.tasks）
同理按需加载。
"""

from typing import TYPE_CHECKING, Any

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "UserInfo",  # noqa: F822
    "UserRole",  # noqa: F822
    "DeptInfo",  # noqa: F822
    "DeptManagerAssignment",  # noqa: F822
    "Post",  # noqa: F822
    "UserSession",  # noqa: F822
    "UserOAuthBinding",  # noqa: F822
    "LdapUserBinding",  # noqa: F822
    "PasswordHistory",  # noqa: F822
    "AccountRisk",  # noqa: F822
    "LoginAccessPolicy",  # noqa: F822
    "UserPasskey",  # noqa: F822
    "PersonalAccessToken",  # noqa: F822
    "ApiApplication",  # noqa: F822
    "ApiApplicationGrant",  # noqa: F822
    "OAuthRefreshToken",  # noqa: F822
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
    "login_success",  # noqa: F822
    "invalidate_menu_user_caches",  # noqa: F822
    # 契约委托函数（common 侧函数级业务 import 的模块级替代）
    "publish_api_quota_warning",
    "apply_grant_fields",
    "apply_grant_row_scope",
    "application_of_request",
    "enforce_application_grant",
    "resolve_request_menu_pk",
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "UserInfo": "identity.models",
    "UserRole": "identity.models",
    "DeptInfo": "identity.models",
    "DeptManagerAssignment": "identity.models",
    "Post": "identity.models",
    "UserSession": "identity.models",
    "UserOAuthBinding": "identity.models",
    "LdapUserBinding": "identity.models",
    "PasswordHistory": "identity.models",
    "AccountRisk": "identity.models",
    "LoginAccessPolicy": "identity.models",
    "UserPasskey": "identity.models",
    "PersonalAccessToken": "identity.models",
    "ApiApplication": "identity.models",
    "ApiApplicationGrant": "identity.models",
    "OAuthRefreshToken": "identity.models",
    "UserInfoSerializer": "identity.serializers.userinfo",
    "invalid_user_cache_signal": "identity.signal",
    # 登录成功处置（服务子模块；import 链重，按需加载）
    "login_success": "identity.services.auth_login",
    # 菜单变更联动的用户缓存失效（identity.signal_handler；Menu 接收器跨域消费）
    "invalidate_menu_user_caches": "identity.signal_handler",
}


if TYPE_CHECKING:
    # 静态类型面显式声明（运行期仍走 PEP 562 惰性加载，避免循环导入）：
    # 供类型化调用方（notifications / utils 等）取到真实类型而非「Any?」占位
    from identity.models import DeptInfo as DeptInfo  # noqa: F401
    from identity.models import DeptManagerAssignment as DeptManagerAssignment  # noqa: F401
    from identity.models import UserInfo as UserInfo  # noqa: F401
    from identity.models import UserRole as UserRole  # noqa: F401


def __getattr__(name: Any) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_superusers() -> Any:
    """超级管理员 queryset（不过滤启用状态，保持历史行为）。"""
    from identity.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True)


def get_active_superuser_queryset() -> Any:
    """在用的超级管理员 queryset。"""
    from identity.models import UserInfo

    return UserInfo.objects.filter(is_superuser=True, is_active=True)


def get_users_by_pks(pks: Any) -> Any:
    """按主键批量取用户。"""
    from identity.models import UserInfo

    return UserInfo.objects.filter(id__in=pks).all()


def get_users_by_perm(perm: Any) -> Any:
    """按单个权限码反查在用用户（get_users_by_perms 单码薄封装）。"""
    return get_users_by_perms([perm])


def get_users_by_perms(perms: Any) -> Any:
    """按权限码清单反查在用用户（任一命中，去重）。

    权限码（"动作:组件名"，如 approve:SystemApprovalRequest）挂 PERMISSION 类型
    菜单的 name，经 角色↔菜单 授权间接授予用户（即"按权限反查"）。
    软删除角色/菜单显式排除，不依赖关联查询的管理器行为。

    通用职能推导工具：审批人解析（approval.utils.approval.get_approver_queryset）
    与通知接收人解析共用，替代逐处复制"角色清单成员查询"。
    """
    from identity.models import UserInfo
    from system.services import Menu

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


def get_active_user_pk_by_username(username: Any) -> Any:
    """按用户名取在用用户主键，不存在返回 None。"""
    from identity.models import UserInfo

    return UserInfo.objects.filter(username=username, is_active=True).values_list("pk", flat=True).first()


def serialize_user_info(user: Any) -> dict[str, Any]:
    """按对外契约序列化用户信息（供 WebSocket 等非 DRF 场景使用）。"""
    from identity.serializers.userinfo import UserInfoSerializer

    typed_value: dict[str, Any] = UserInfoSerializer(instance=user).data
    return typed_value


def register_user_session(request: Any, user: Any, login_type: Any, channel_name: str = "") -> Any:
    """登录/WS 接入时登记会话（identity.utils.session 契约导出，供 message app 使用）。"""
    from identity.utils.session import register_user_session as _register

    return _register(request, user, login_type, channel_name=channel_name)


def websocket_session_logout(channel_name: Any) -> None:
    """WS 优雅断开时按 channel 置会话离线（异常残留由保留期清理任务兜底）。"""
    from django.utils import timezone

    from identity.models import UserSession

    UserSession.objects.filter(channel_name=channel_name, status=UserSession.Status.ONLINE).update(
        status=UserSession.Status.OFFLINE, last_active=timezone.now()
    )


# ---------------------------------------------------------------------------
# 契约委托函数：common 侧的函数级业务 import 收敛为本模块的模块级契约缝。
# 委托体保持调用期惰性加载——与原函数级 import 等价（循环依赖 / 迁移期降级
# 语义不变）。新增委托时同步登记 CONTRACT_SEAMS 并更新 __all__。
# ---------------------------------------------------------------------------


def publish_api_quota_warning(info: Any) -> None:
    """API 配额告警：系统消息 + 出站 Webhook（identity.notifications 契约导出）。"""
    from identity.notifications import ApiQuotaWarningMessage

    ApiQuotaWarningMessage(info).publish(is_async=True)


def apply_grant_fields(request: Any, model_label: Any, allowed: Any) -> Any:
    """应用凭证字段授权：可见字段收敛（identity.utils.api_grant 契约导出）。"""
    from identity.utils.api_grant import apply_grant_fields as _apply

    return _apply(request, model_label, allowed)


def apply_grant_row_scope(request: Any, queryset: Any) -> Any:
    """应用凭证行级授权：queryset 收敛（identity.utils.api_grant 契约导出）。"""
    from identity.utils.api_grant import apply_grant_row_scope as _apply

    return _apply(request, queryset)


def application_of_request(request: Any) -> Any:
    """请求关联的应用凭证（identity.utils.api_grant 契约导出）。"""
    from identity.utils.api_grant import application_of_request as _resolve

    return _resolve(request)


def enforce_application_grant(request: Any, view: Any) -> Any:
    """应用凭证权限点校验（identity.utils.api_grant 契约导出）。"""
    from identity.utils.api_grant import enforce_application_grant as _enforce

    return _enforce(request, view)


def resolve_request_menu_pk(request: Any) -> Any:
    """按请求路径解析应用凭证菜单（identity.utils.api_grant 契约导出）。"""
    from identity.utils.api_grant import resolve_request_menu_pk as _resolve

    return _resolve(request)
