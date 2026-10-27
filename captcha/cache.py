#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码的挑战 / 尝试计数缓存（票务签发与消费见 ``captcha.tickets``）。

挑战是 2 分钟级短命数据，走 django-redis（与 oauth_state /
passkey challenge 同类）；图片题型同样只写挑战缓存，不再落 DB。

键空间（均已登记 check_cache_keys 与 docs/architecture/cache.md）：

- ``captcha_challenge_``：挑战密文（答案在服务端，TTL 默认 120s）；
- ``captcha_attempt_``：挑战已尝试次数（与挑战同 TTL）；
- ``captcha_ticket_inflight_``：同 IP 未消费 ticket 计数（TTL 300s）。
"""

from typing import Any

from django.core.cache import cache

CHALLENGE_KEY_PREFIX = "captcha_challenge"
ATTEMPT_KEY_PREFIX = "captcha_attempt"
TICKET_INFLIGHT_KEY_PREFIX = "captcha_ticket_inflight"

#: 未消费 ticket 计数窗口：与 ticket TTL 相比留余量（覆盖慢速重放与时钟漂移）
TICKET_INFLIGHT_TTL = 300
#: 同 IP 最多持有的未消费 ticket 数（防囤票后慢速重放）
TICKET_INFLIGHT_LIMIT = 3


def _challenge_key(challenge_id: str) -> str:
    return f"{CHALLENGE_KEY_PREFIX}_{challenge_id}"


def _attempt_key(challenge_id: str) -> str:
    return f"{ATTEMPT_KEY_PREFIX}_{challenge_id}"


def _inflight_key(ip: str) -> str:
    return f"{TICKET_INFLIGHT_KEY_PREFIX}_{ip or 'unknown'}"


def store_challenge(challenge_id: str, payload: dict[str, Any], ttl: int) -> None:
    cache.set(_challenge_key(challenge_id), payload, ttl)


def load_challenge(challenge_id: str) -> dict[str, Any] | None:
    value = cache.get(_challenge_key(challenge_id))
    return value if isinstance(value, dict) else None


def delete_challenge(challenge_id: str) -> None:
    cache.delete(_challenge_key(challenge_id))
    cache.delete(_attempt_key(challenge_id))


def incr_attempt(challenge_id: str, ttl: int) -> int:
    """挑战尝试次数自增；键不存在时从 1 起算（窗口与挑战同寿）。"""
    key = _attempt_key(challenge_id)
    if cache.add(key, 1, ttl):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        # 键在 add 与 incr 之间过期：重建计数，本次按第一次尝试处理
        cache.set(key, 1, ttl)
        return 1


def reserve_ticket_slot(ip: str) -> bool:
    """占用一个「未消费 ticket」名额；超过上限返回 False。"""
    key = _inflight_key(ip)
    if cache.add(key, 1, TICKET_INFLIGHT_TTL):
        return True
    try:
        count = int(cache.incr(key))
    except ValueError:
        cache.set(key, 1, TICKET_INFLIGHT_TTL)
        return True
    if count > TICKET_INFLIGHT_LIMIT:
        try:
            cache.decr(key)
        except ValueError:
            pass
        return False
    return True


def release_ticket_slot(ip: str) -> None:
    key = _inflight_key(ip)
    try:
        if int(cache.decr(key)) <= 0:
            cache.delete(key)
    except (ValueError, TypeError):
        # 键已自然过期或不是计数：无需回收
        pass
