#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/10/2024


from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from common.utils import ip


class BlockUtil:
    BLOCK_KEY_TMPL: str

    def __init__(self, username: str) -> None:
        self.block_key = self.BLOCK_KEY_TMPL.format(username)
        self.key_ttl = int(settings.SECURITY_LOGIN_LIMIT_TIME) * 60

    def block(self) -> None:
        cache.set(self.block_key, True, self.key_ttl)

    def is_block(self) -> bool:
        return bool(cache.get(self.block_key))


class BlockUtilBase:
    LIMIT_KEY_TMPL: str
    BLOCK_KEY_TMPL: str
    #: 用户级「全局」计数键模板（可选）：设置后按 (用户, IP) 与「用户」双键计数，
    #: 任一超限即锁。仅按 (用户, IP) 计数时，攻击者轮换出口 IP 即可拿到无限次
    #: 独立额度（验证码空间有限的高价值目标必须叠加用户维度总闸）。
    USER_LIMIT_KEY_TMPL: str = ""

    def __init__(self, username: str, ip: str) -> None:
        self.username = username
        self.ip = ip
        self.limit_key = self.LIMIT_KEY_TMPL.format(username, ip)
        self.user_limit_key = self.USER_LIMIT_KEY_TMPL.format(username) if self.USER_LIMIT_KEY_TMPL else ""
        self.block_key = self.BLOCK_KEY_TMPL.format(username)
        self.key_ttl = int(settings.SECURITY_LOGIN_LIMIT_TIME) * 60

    def get_remainder_times(self) -> int:
        times_up = settings.SECURITY_LOGIN_LIMIT_COUNT
        times_failed = self.get_failed_count()
        times_remainder = int(times_up) - int(times_failed)
        return times_remainder

    def incr_failed_count(self) -> int:
        count = self._incr(self.limit_key)
        # 用户维度计数与 (用户, IP) 并行：任一达到上限即锁定
        user_count = self._incr(self.user_limit_key) if self.user_limit_key else count
        limit_count = int(settings.SECURITY_LOGIN_LIMIT_COUNT)
        if count >= limit_count or user_count >= limit_count:
            cache.set(self.block_key, True, self.key_ttl)
        return limit_count - max(count, user_count)

    @staticmethod
    def _incr(key: str) -> int:
        count = int(cache.get(key, 0) or 0)
        count += 1
        cache.set(key, count, int(settings.SECURITY_LOGIN_LIMIT_TIME) * 60)
        return count

    def get_failed_count(self) -> int:
        count = int(cache.get(self.limit_key, 0) or 0)
        if self.user_limit_key:
            count = max(count, int(cache.get(self.user_limit_key, 0) or 0))
        return count

    def clean_failed_count(self) -> None:
        cache.delete(self.limit_key)
        if self.user_limit_key:
            cache.delete(self.user_limit_key)
        cache.delete(self.block_key)

    @classmethod
    def unblock_user(cls, username: str) -> None:
        key_limit = cls.LIMIT_KEY_TMPL.format(username, "*")
        key_block = cls.BLOCK_KEY_TMPL.format(username)
        # Redis 尽量不要用通配
        cache.delete_pattern(key_limit)
        if cls.USER_LIMIT_KEY_TMPL:
            cache.delete(cls.USER_LIMIT_KEY_TMPL.format(username))
        cache.delete(key_block)

    @classmethod
    def is_user_block(cls, username: str) -> bool:
        block_key = cls.BLOCK_KEY_TMPL.format(username)
        return bool(cache.get(block_key))

    @classmethod
    def get_users_block(cls, usernames: Iterable[str]) -> dict[str, bool]:
        """批量获取多个用户是否处于锁定状态，一次 get_many 替代逐用户访问缓存"""
        if not usernames:
            return {}
        key_username = {cls.BLOCK_KEY_TMPL.format(username): username for username in set(usernames)}
        cached = cache.get_many(key_username)
        return {username: bool(cached.get(key)) for key, username in key_username.items()}

    def is_block(self) -> bool:
        return bool(cache.get(self.block_key))


class BlockGlobalIpUtilBase:
    LIMIT_KEY_TMPL: str
    BLOCK_KEY_TMPL: str

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.limit_key = self.LIMIT_KEY_TMPL.format(ip)
        self.block_key = self.BLOCK_KEY_TMPL.format(ip)
        self.key_ttl = int(settings.SECURITY_LOGIN_IP_LIMIT_TIME) * 60

    @property
    def ip_in_black_list(self) -> bool:
        return ip.contains_ip(self.ip, settings.SECURITY_LOGIN_IP_BLACK_LIST)

    @property
    def ip_in_white_list(self) -> bool:
        return ip.contains_ip(self.ip, settings.SECURITY_LOGIN_IP_WHITE_LIST)

    def set_block_if_need(self) -> None:
        if self.ip_in_white_list or self.ip_in_black_list:
            return
        count = int(cache.get(self.limit_key, 0) or 0)
        count += 1
        cache.set(self.limit_key, count, self.key_ttl)

        limit_count = int(settings.SECURITY_LOGIN_IP_LIMIT_COUNT)
        if count < limit_count:
            return
        cache.set(self.block_key, timezone.now().isoformat(), self.key_ttl)

    def clean_block_if_need(self) -> None:
        cache.delete(self.limit_key)
        cache.delete(self.block_key)

    def is_block(self) -> bool:
        if self.ip_in_white_list:
            return False
        if self.ip_in_black_list:
            return True
        return bool(cache.get(self.block_key))

    def get_block_info(self) -> Any:
        try:
            data = cache.get(self.block_key)
            if data:
                return parse_datetime(data)
            return "N/A"
        except Exception:
            # 缓存不可用：返回 N/A（解除时间查询为展示用途，不阻断登录）
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
    # 双键计数：单一 (用户, IP) 计数可被轮换出口 IP 绕过（TOTP 6 位码空间有限，
    # 多 IP 并行在线爆破不可接受），叠加用户维度总闸后任一超限即锁。
    USER_LIMIT_KEY_TMPL = "_MFA_LIMIT_USER_{}"


class LoginIpBlockUtil(BlockGlobalIpUtilBase):
    LIMIT_KEY_TMPL = "_LOGIN_LIMIT_{}"
    BLOCK_KEY_TMPL = "_LOGIN_BLOCK_IP_{}"
