#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : cache
import hashlib
import time

from django.conf import settings
from django.core.cache import cache

from mfa.const import CONFIRM_TYPE_LEVEL, CONFIRM_TYPE_TTL_SETTING


def _cache_prefix(name, default):
    return settings.CACHE_KEY_TEMPLATE.get(name, default)


class UserConfirmStateCache:
    """敏感操作二次确认状态缓存

    项目使用 JWT 认证（无 session），验证通过后的确认状态存入 Redis：
    {'level': 确认级别, 'type': 确认类型, 'method': 验证方式, 'time': 确认时间}
    有效期按确认类型对应的 settings TTL 计算，高级别确认可满足低级别要求。
    """

    def __init__(self, user):
        self.user = user
        self.cache_key = f'{_cache_prefix("mfa_confirm_state_key", "mfa_confirm_state")}_{user.pk}'

    def get(self):
        return cache.get(self.cache_key)

    def set(self, confirm_type, method):
        cache.set(self.cache_key, {
            'level': CONFIRM_TYPE_LEVEL[confirm_type],
            'type': confirm_type,
            'method': method,
            'time': time.time(),
        }, int(getattr(settings, CONFIRM_TYPE_TTL_SETTING[confirm_type])))

    def clear(self):
        cache.delete(self.cache_key)

    def is_valid_for(self, confirm_type):
        """当前确认状态是否满足指定验证类型（级别足够且未过有效期）"""
        state = self.get()
        if not state:
            return False
        if state.get('level', 0) < CONFIRM_TYPE_LEVEL[confirm_type]:
            return False
        ttl = int(getattr(settings, CONFIRM_TYPE_TTL_SETTING[state['type']]))
        return time.time() - state.get('time', 0) <= ttl


class OtpBindCache:
    """OTP 绑定候选密钥缓存（二次验证通过前不入库，防止未验证的密钥污染账号）"""

    TIMEOUT = 10 * 60

    def __init__(self, user):
        self.user = user
        self.cache_key = f'{_cache_prefix("mfa_otp_bind_key", "mfa_otp_bind")}_{user.pk}'

    def get_secret(self):
        return cache.get(self.cache_key)

    def set_secret(self, secret):
        cache.set(self.cache_key, secret, self.TIMEOUT)

    def clear(self):
        cache.delete(self.cache_key)


class UsedOtpCodeCache:
    """OTP 防重放缓存：同一动态码在有效窗口内只允许使用一次"""

    TIMEOUT = 90

    def __init__(self, user, code):
        code_md5 = hashlib.md5(str(code).encode()).hexdigest()
        self.cache_key = f'{_cache_prefix("mfa_otp_used_key", "mfa_otp_used")}_{user.pk}_{code_md5}'

    def exists(self):
        return bool(cache.get(self.cache_key))

    def mark(self):
        cache.set(self.cache_key, True, self.TIMEOUT)
