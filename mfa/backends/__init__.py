#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : backends
"""MFA 验证后端注册表（策略模式）

新增验证方式：实现 BaseMFA 子类后加入 MFA_BACKEND_CLASSES 即可，
配置层面通过 SECURITY_MFA_CONFIRM_BACKENDS 控制启用哪些方式。

认证方式策略在此统一收敛：全局白名单（SECURITY_MFA_METHODS）
∩ 角色级 allowed_mfa_types ∩ 用户级 allowed_mfa_types —— 角色 / 用户只能收窄。
"""

from django.conf import settings

from common.utils import get_logger
from mfa.backends.base import BaseMFA
from mfa.backends.email import EmailBackend
from mfa.backends.otp import OtpBackend
from mfa.backends.passkey import PasskeyBackend
from mfa.backends.password import PasswordBackend
from mfa.backends.sms import SmsBackend

logger = get_logger(__name__)

__all__ = [
    "BaseMFA",
    "OtpBackend",
    "SmsBackend",
    "EmailBackend",
    "PasswordBackend",
    "PasskeyBackend",
    "MFA_BACKEND_CLASSES",
    "get_backend",
    "get_enabled_backends",
    "get_user_mfa_policy",
]

MFA_BACKEND_CLASSES = [OtpBackend, SmsBackend, EmailBackend, PasswordBackend, PasskeyBackend]


def _normalize_methods(value):
    return {str(item).strip() for item in (value or []) if str(item).strip()}


def get_user_mfa_policy(user) -> dict:
    """认证方式策略：返回 {"methods": set|None, "mfa_required": bool}。

    - ``methods``：可用方式集合 = 全局白名单 ∩ 角色允许集 ∩ 用户允许集；
      各维度为空 = 该维度不限制，全部为空时返回 None（不限制）；
    - ``mfa_required``：任一启用角色声明 mfa_required 即强制（登录时要求二次验证）。
    """
    policy = {"methods": None, "mfa_required": False}
    if user is None or not getattr(user, "pk", None):
        return policy

    layers = []
    global_methods = _normalize_methods(getattr(settings, "SECURITY_MFA_METHODS", []))
    if global_methods:
        layers.append(global_methods)

    try:
        for role in user.roles.filter(is_active=True):
            role_methods = _normalize_methods(role.allowed_mfa_types)
            if role_methods:
                layers.append(role_methods)
            if role.mfa_required:
                policy["mfa_required"] = True
    except Exception:  # noqa: BLE001 角色查询异常不阻断验证方式判定
        logger.warning("load role mfa policy failed. user:%s", user.pk, exc_info=True)

    user_methods = _normalize_methods(getattr(user, "allowed_mfa_types", []))
    if user_methods:
        layers.append(user_methods)

    if not layers:
        return policy
    effective = set(layers[0])
    for layer in layers[1:]:
        effective &= layer
    policy["methods"] = effective
    return policy


def _method_allowed(user, name) -> bool:
    methods = get_user_mfa_policy(user).get("methods")
    return methods is None or name in methods


def get_backend(user, name, request=None):
    """按名称获取指定验证后端（须全局启用、策略允许且用户可用），不可用返回 None"""
    for cls in MFA_BACKEND_CLASSES:
        if cls.name != name or not cls.global_enabled():
            continue
        if not _method_allowed(user, cls.name):
            return None
        backend = cls(user, request=request)
        if backend.is_active():
            return backend
    return None


def get_enabled_backends(user, request=None, levels=None):
    """获取用户当前可用的全部验证方式，可按确认级别过滤（levels 为 ConfirmType 列表）"""
    backends = []
    for cls in MFA_BACKEND_CLASSES:
        if not cls.global_enabled():
            continue
        if levels and cls.confirm_level not in levels:
            continue
        if not _method_allowed(user, cls.name):
            continue
        backend = cls(user, request=request)
        if backend.is_active():
            backends.append(backend)
    return backends
