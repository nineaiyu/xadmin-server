#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : confirm
"""敏感操作二次身份验证的通用接入点（仿 JumpServer UserConfirmation）

三种使用方式：
1. DRF ViewSet：permission_classes = [IsAuthenticated, UserConfirmation.require(ConfirmType.MFA)]
2. 视图方法/任意函数内手动校验：ensure_user_confirmed(request, ConfirmType.PASSWORD)
3. 装饰器：@require_user_confirmation(ConfirmType.PASSWORD)

未通过验证时统一抛出 HTTP 412（type=user_confirm_required），由前端拦截并弹出
验证弹窗；验证通过后确认状态写入 Redis（JWT 无 session），有效期内免重复验证。
"""
import functools

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

from common.utils import get_logger
from mfa.cache import UserConfirmStateCache
from mfa.const import CONFIRM_TYPE_LEVEL, ConfirmType
from mfa.exceptions import MFAConfirmRequired

logger = get_logger(__name__)


def check_user_confirm(user, confirm_type=ConfirmType.MFA):
    """校验用户是否在有效期内通过过二次确认，未通过则抛出 412 异常"""
    if not settings.SECURITY_MFA_CONFIRM_ENABLED:
        return
    if not UserConfirmStateCache(user).is_valid_for(confirm_type):
        raise MFAConfirmRequired(confirm_type)


class UserConfirmation(BasePermission):
    """敏感操作二次验证权限类

    用法（敏感 API 声明即接入）：
        permission_classes = [IsAuthenticated, UserConfirmation.require(ConfirmType.MFA)]
    """

    min_type = ConfirmType.MFA

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            # 未认证交给认证层处理，这里只负责已认证用户的二次确认
            return True
        check_user_confirm(user, self.min_type)
        return True

    @classmethod
    def require(cls, confirm_type=ConfirmType.MFA):
        """按验证类型动态生成权限类（级别语义见 ConfirmType）"""
        name = f'UserConfirmationLevel{CONFIRM_TYPE_LEVEL[confirm_type]}'
        return type(name, (cls,), {'min_type': confirm_type})


def ensure_user_confirmed(request, confirm_type=ConfirmType.MFA):
    """非 DRF 视图场景（业务方法/定时任务回调等）手动执行二次确认校验"""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        raise MFAConfirmRequired(confirm_type, detail=_('Authentication required'))
    check_user_confirm(user, confirm_type)


def require_user_confirmation(confirm_type=ConfirmType.MFA):
    """装饰器：标注敏感业务函数/方法，调用前先执行二次确认校验

    自动从位置参数或关键字参数中定位 request 对象（兼容 self, request 调用形态）。
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            request = next((arg for arg in args if hasattr(arg, 'user')), None)
            if request is None:
                request = kwargs.get('request')
            if request is None:
                raise MFAConfirmRequired(confirm_type, detail=_('Request object not found'))
            ensure_user_confirmed(request, confirm_type)
            return func(*args, **kwargs)

        return wrapper

    return decorator
