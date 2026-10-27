#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码的挑战 / 尝试计数缓存（票务签发与消费见 ``captcha.tickets``）。

挑战是 2 分钟级短命数据，走 django-redis（与 oauth_state /
passkey challenge 同类）；图片题型同样只写挑战缓存，不再落 DB。

键空间（均已登记 check_cache_keys 与 docs/architecture/cache.md）：

- ``captcha_challenge_``：挑战密文（答案在服务端，TTL 默认 120s）；
- ``captcha_attempt_``：挑战已尝试次数（与挑战同 TTL）；
- ``captcha_challenge_lock_``：挑战校验单飞锁（防并发双签票据，TTL 15s）；
- ``captcha_ticket_inflight_``：同 IP 未消费 ticket 计数（TTL 300s）。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.core.cache import cache

CHALLENGE_KEY_PREFIX = "captcha_challenge"
ATTEMPT_KEY_PREFIX = "captcha_attempt"
CHALLENGE_LOCK_PREFIX = "captcha_challenge_lock"
TICKET_INFLIGHT_KEY_PREFIX = "captcha_ticket_inflight"

#: 单飞锁 TTL：仅覆盖一次校验的耗时；进程异常退出后靠过期自动释放
CHALLENGE_LOCK_TTL = 15
#: 未消费 ticket 计数窗口：与 ticket TTL 相比留余量（覆盖慢速重放与时钟漂移）
TICKET_INFLIGHT_TTL = 300
#: 同 IP 最多持有的未消费 ticket 数（防囤票后慢速重放）
TICKET_INFLIGHT_LIMIT = 3


def _challenge_key(challenge_id: str) -> str:
    return f"{CHALLENGE_KEY_PREFIX}_{challenge_id}"


def _attempt_key(challenge_id: str) -> str:
    return f"{ATTEMPT_KEY_PREFIX}_{challenge_id}"


def _challenge_lock_key(challenge_id: str) -> str:
    return f"{CHALLENGE_LOCK_PREFIX}_{challenge_id}"


def _inflight_key(ip: str) -> str:
    from captcha.signals import hash_value

    # 精确 IP 的加盐哈希（不是 /24 网段）：Redis 不落明文，同时避免同一网段
    # 的多个用户共享「未消费 ticket ≤3」的名额而互相顶掉。
    return f"{TICKET_INFLIGHT_KEY_PREFIX}_{hash_value(ip) or 'unknown'}"


def store_challenge(challenge_id: str, payload: dict[str, Any], ttl: int) -> None:
    cache.set(_challenge_key(challenge_id), payload, ttl)


def load_challenge(challenge_id: str) -> dict[str, Any] | None:
    value = cache.get(_challenge_key(challenge_id))
    return value if isinstance(value, dict) else None


def delete_challenge(challenge_id: str) -> None:
    cache.delete(_challenge_key(challenge_id))
    cache.delete(_attempt_key(challenge_id))


def acquire_challenge_lock(challenge_id: str, ttl: int = CHALLENGE_LOCK_TTL) -> bool:
    """挑战校验单飞锁：同一挑战并发校验只允许一个请求进入（cache.add 原子占位）。"""
    try:
        return bool(cache.add(_challenge_lock_key(challenge_id), 1, ttl))
    except Exception:  # noqa: BLE001 锁不可用 = 拒绝本次校验（fail-closed）
        return False


def release_challenge_lock(challenge_id: str) -> None:
    """释放单飞锁（成功删挑战后 / 失败与异常路径都必须调用）。"""
    try:
        cache.delete(_challenge_lock_key(challenge_id))
    except Exception:  # noqa: BLE001 锁 TTL 会自动兜底
        pass


@contextmanager
def challenge_guard(challenge_id: str) -> Iterator[bool]:
    """挑战校验单飞守卫：yield 是否抢到锁；仅抢到锁的请求负责释放。"""
    acquired = acquire_challenge_lock(challenge_id)
    try:
        yield acquired
    finally:
        if acquired:
            release_challenge_lock(challenge_id)


def incr_counter(key: str, ttl: int) -> int:
    """原子窗口计数：键不存在时从 1 起算并设置 TTL（并发下不丢增量）。"""
    if cache.add(key, 1, ttl):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        # 键在 add 与 incr 之间过期：重建计数，本次按第一次计数处理
        cache.set(key, 1, ttl)
        return 1


def incr_attempt(challenge_id: str, ttl: int) -> int:
    """挑战尝试次数自增；键不存在时从 1 起算（窗口与挑战同寿）。"""
    return incr_counter(_attempt_key(challenge_id), ttl)


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
