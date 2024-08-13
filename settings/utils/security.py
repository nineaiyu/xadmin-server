#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/10/2024


from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.utils import ip


class BlockUtil:
    BLOCK_KEY_TMPL: str

    def __init__(self, username):
        self.block_key = self.BLOCK_KEY_TMPL.format(username)
        self.key_ttl = int(settings.SECURITY_LOGIN_LIMIT_TIME) * 60

    def block(self):
        cache.set(self.block_key, True, self.key_ttl)

    def is_block(self):
        return bool(cache.get(self.block_key))


class BlockUtilBase:
    LIMIT_KEY_TMPL: str
    BLOCK_KEY_TMPL: str

    def __init__(self, username, ip):
        self.username = username
        self.ip = ip
        self.limit_key = self.LIMIT_KEY_TMPL.format(username, ip)
        self.block_key = self.BLOCK_KEY_TMPL.format(username)
        self.key_ttl = int(settings.SECURITY_LOGIN_LIMIT_TIME) * 60

    def get_remainder_times(self):
        times_up = settings.SECURITY_LOGIN_LIMIT_COUNT
        times_failed = self.get_failed_count()
        times_remainder = int(times_up) - int(times_failed)
        return times_remainder

    def incr_failed_count(self) -> int:
        limit_key = self.limit_key
        count = cache.get(limit_key, 0)
        count += 1
        cache.set(limit_key, count, self.key_ttl)

        limit_count = settings.SECURITY_LOGIN_LIMIT_COUNT
        if count >= limit_count:
            cache.set(self.block_key, True, self.key_ttl)
        return limit_count - count

    def get_failed_count(self):
        count = cache.get(self.limit_key, 0)
        return count

    def clean_failed_count(self):
        cache.delete(self.limit_key)
        cache.delete(self.block_key)

    @classmethod
    def unblock_user(cls, username):
        key_limit = cls.LIMIT_KEY_TMPL.format(username, '*')
        key_block = cls.BLOCK_KEY_TMPL.format(username)
        # Redis 尽量不要用通配
        cache.delete_pattern(key_limit)
        cache.delete(key_block)

    @classmethod
    def is_user_block(cls, username):
        block_key = cls.BLOCK_KEY_TMPL.format(username)
        return bool(cache.get(block_key))

    def is_block(self):
        return bool(cache.get(self.block_key))


class BlockGlobalIpUtilBase:
    LIMIT_KEY_TMPL: str
    BLOCK_KEY_TMPL: str

    def __init__(self, ip):
        self.ip = ip
        self.limit_key = self.LIMIT_KEY_TMPL.format(ip)
        self.block_key = self.BLOCK_KEY_TMPL.format(ip)
        self.key_ttl = int(settings.SECURITY_LOGIN_IP_LIMIT_TIME) * 60

    @property
    def ip_in_black_list(self):
        return ip.contains_ip(self.ip, settings.SECURITY_LOGIN_IP_BLACK_LIST)

    @property
    def ip_in_white_list(self):
        return ip.contains_ip(self.ip, settings.SECURITY_LOGIN_IP_WHITE_LIST)

    def set_block_if_need(self):
        if self.ip_in_white_list or self.ip_in_black_list:
            return
        count = cache.get(self.limit_key, 0)
        count += 1
        cache.set(self.limit_key, count, self.key_ttl)

        limit_count = settings.SECURITY_LOGIN_IP_LIMIT_COUNT
        if count < limit_count:
            return
        cache.set(self.block_key, timezone.now().isoformat(), self.key_ttl)

    def clean_block_if_need(self):
        cache.delete(self.limit_key)
        cache.delete(self.block_key)

    def is_block(self):
        if self.ip_in_white_list:
            return False
        if self.ip_in_black_list:
            return True
        return bool(cache.get(self.block_key))

    def get_block_info(self):
        try:
            data = cache.get(self.block_key)
            if data:
                return parse_datetime(data)
            return "N/A"
        except:
            return "N/A"


class LoginBlockUtil(BlockUtilBase):
    LIMIT_KEY_TMPL = "_LOGIN_LIMIT_{}_{}"
    BLOCK_KEY_TMPL = "_LOGIN_BLOCK_{}"


class ResetBlockUtil(BlockUtilBase):
    LIMIT_KEY_TMPL = "_RESET_LIMIT_{}_{}"
    BLOCK_KEY_TMPL = "_RESET_BLOCK_{}"


class RegisterBlockUtil(BlockUtilBase):
    LIMIT_KEY_TMPL = "_REGISTER_LIMIT_{}_{}"
    BLOCK_KEY_TMPL = "_REGISTER_BLOCK_{}"


class SendVerifyCodeBlockUtil(BlockUtilBase):
    LIMIT_KEY_TMPL = "_SEND_VERIFY_CODE_LIMIT_{}_{}"
    BLOCK_KEY_TMPL = "_SEND_VERIFY_CODE_BLOCK_{}"


class MFABlockUtils(BlockUtilBase):
    LIMIT_KEY_TMPL = "_MFA_LIMIT_{}_{}"
    BLOCK_KEY_TMPL = "_MFA_BLOCK_{}"


class LoginIpBlockUtil(BlockGlobalIpUtilBase):
    LIMIT_KEY_TMPL = "_LOGIN_LIMIT_{}"
    BLOCK_KEY_TMPL = "_LOGIN_BLOCK_IP_{}"
