#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : services
"""mfa app 对外服务契约层。

其他 app 需要使用敏感操作二次验证 / 登录 MFA 能力时，只允许从本模块导入。
核心用法见 docs/architecture/mfa.md。
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from common.utils.request import get_request_ip
from common.utils.verify_code import TokenTempCache
from mfa.backends import get_backend, get_enabled_backends
from mfa.cache import UserConfirmStateCache
from mfa.const import CONFIRM_TYPE_LEVEL, ConfirmType
from settings.services import MFABlockUtils

logger = get_logger(__name__)


def _get_request_ip(request):
    return get_request_ip(request) if request else ''


def _serialize_backend(backend):
    return {
        'name': backend.name,
        'display_name': str(backend.display_name),
        'placeholder': str(backend.placeholder),
        'challenge_required': backend.challenge_required,
    }


def _check_mfa_block(user, ipaddr):
    """MFA 验证防爆破锁定校验，返回锁定提示文案（未锁定返回 None）"""
    if MFABlockUtils(user.username, ipaddr).is_block():
        return _('Too many failures, the account has been locked '
                 '(please contact admin to unlock it or try again after {} minutes)'
                 ).format(settings.SECURITY_LOGIN_LIMIT_TIME)
    return None


def get_confirm_methods(user, request=None, confirm_type=ConfirmType.MFA):
    """获取用户在指定验证类型下可用的验证方式（低级别请求允许使用高级别方式）"""
    if confirm_type == ConfirmType.PASSWORD:
        levels = [ConfirmType.MFA, ConfirmType.PASSWORD]
    else:
        levels = [ConfirmType.MFA]
    return [_serialize_backend(b) for b in get_enabled_backends(user, request=request, levels=levels)]


def check_user_mfa_code(user, method, code, request=None):
    """校验动态验证码（含防爆破锁定），返回 (是否通过, 失败原因)"""
    ipaddr = _get_request_ip(request)
    locked = _check_mfa_block(user, ipaddr)
    if locked:
        return False, str(locked)

    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _('The verification method is unavailable')

    ok, msg = backend.check_code(code)
    block = MFABlockUtils(user.username, ipaddr)
    if ok:
        block.clean_failed_count()
        return True, ''
    block.incr_failed_count()
    return False, msg


def verify_user_confirm(user, method, code, request=None, confirm_type=ConfirmType.MFA):
    """校验验证码并写入二次确认状态（有效期内敏感操作免重复验证）"""
    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _('The verification method is unavailable')
    if CONFIRM_TYPE_LEVEL[backend.confirm_level] < CONFIRM_TYPE_LEVEL[confirm_type]:
        return False, _('The verification method does not meet the security requirements')

    ok, msg = check_user_mfa_code(user, method, code, request=request)
    if ok:
        UserConfirmStateCache(user).set(backend.confirm_level, method)
    return ok, msg


def send_user_mfa_code(user, method, request=None):
    """下发挑战验证码（短信/邮件），返回 (是否成功, 失败原因)"""
    backend = get_backend(user, method, request=request)
    if not backend:
        return False, _('The verification method is unavailable')
    if not backend.challenge_required:
        return False, _('This method does not need a verification code to be sent')
    return backend.send_challenge()


def is_login_mfa_required(user) -> bool:
    """判断用户登录是否需要 MFA 二次验证（绑定 OTP 后自动生效，全局开关可关闭）"""
    if not settings.SECURITY_MFA_LOGIN_PROTECT_ENABLED:
        return False
    return user.mfa_enabled


def generate_login_mfa_token(user) -> str:
    """生成登录 MFA 临时令牌（不含任何真实凭证，一次性使用）"""
    return TokenTempCache.generate_cache_token(
        settings.SECURITY_MFA_LOGIN_TOKEN_TTL, {'user_id': user.pk, 'scene': 'login_mfa'}
    )


def validate_login_mfa_token(token):
    """校验登录 MFA 临时令牌，返回对应用户（无效或已禁用返回 None）"""
    data = TokenTempCache.validate_cache_token(token)
    if not data or data.get('scene') != 'login_mfa':
        return None
    return get_user_model().objects.filter(pk=data.get('user_id'), is_active=True).first()


def get_login_mfa_methods(user, request=None):
    """获取登录 MFA 可用的验证方式（密码方式在登录场景无意义，不参与）"""
    return get_confirm_methods(user, request=request, confirm_type=ConfirmType.MFA)
