#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : backends
"""MFA 验证后端注册表（策略模式）

新增验证方式：实现 BaseMFA 子类后加入 MFA_BACKEND_CLASSES 即可，
配置层面通过 SECURITY_MFA_CONFIRM_BACKENDS 控制启用哪些方式。
"""
from mfa.backends.base import BaseMFA
from mfa.backends.email import EmailBackend
from mfa.backends.otp import OtpBackend
from mfa.backends.password import PasswordBackend
from mfa.backends.sms import SmsBackend

__all__ = ['BaseMFA', 'OtpBackend', 'SmsBackend', 'EmailBackend', 'PasswordBackend',
           'MFA_BACKEND_CLASSES', 'get_backend', 'get_enabled_backends']

MFA_BACKEND_CLASSES = [OtpBackend, SmsBackend, EmailBackend, PasswordBackend]


def get_backend(user, name, request=None):
    """按名称获取指定验证后端（须全局启用且用户可用），不可用返回 None"""
    for cls in MFA_BACKEND_CLASSES:
        if cls.name != name or not cls.global_enabled():
            continue
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
        backend = cls(user, request=request)
        if backend.is_active():
            backends.append(backend)
    return backends
