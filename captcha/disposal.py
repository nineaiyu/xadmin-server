#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""处置层（L4）：三维度阶梯计数 / 冷却 / 登录锁定联动。

复用项目既有的「按维度计数 + 超限锁定」范式，但计数维度是验证码场景的
IP / 动作标识 / 指纹；登录锁定联动直接调用现成的 ``LoginBlockUtil``。
冷却期内一律返回与答案错误同形的失败响应（不告诉对方「你被封了」）。
"""

from typing import Any

from django.conf import settings
from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)

DIM_FAIL_PREFIX = "captcha_dim_fail"
COOLDOWN_PREFIX = "captcha_cooldown"
#: 计数窗口 1 小时：阶梯第三档（20 次/1h）必须能数到，不能用 900s
FAIL_WINDOW = 3600
COOLDOWN_SECONDS = {"first": 0, "second": 900, "third": 3600}


def parse_steps() -> tuple[int, int, int]:
    """解析 CAPTCHA_FAIL_STEP（如 ``3,8,20``）；非法 / 全 0 = 停用处置。"""
    raw = str(getattr(settings, "CAPTCHA_FAIL_STEP", "3,8,20") or "")
    values = []
    for item in raw.split(","):
        try:
            values.append(max(int(item.strip()), 0))
        except ValueError:
            values.append(0)
    while len(values) < 3:
        values.append(0)
    first, second, third = values[:3]
    if first <= 0 or second <= 0 or third <= 0:
        return 0, 0, 0
    return first, second, third


def dimension_keys(*, ip_hash: str = "", action: str = "", fp: str = "", account: str = "") -> list[tuple[str, str]]:
    """参与处置的维度（空值跳过）。动作维度用 scene/action 常量，不存明文账号。"""
    dims: list[tuple[str, str]] = []
    if ip_hash:
        dims.append(("ip", ip_hash))
    if action:
        dims.append(("action", action))
    if fp:
        dims.append(("fp", fp))
    if account:
        dims.append(("acct", account))
    return dims


def _fail_key(dim: str, value: str) -> str:
    return f"{DIM_FAIL_PREFIX}_{dim}_{value}"


def _cooldown_key(dim: str, value: str) -> str:
    return f"{COOLDOWN_PREFIX}_{dim}_{value}"


def cooldown_remaining(dims: list[tuple[str, str]]) -> int:
    """返回剩余冷却秒数（0 = 未冷却）；任一维度处于冷却即生效。"""
    remaining = 0
    for dim, value in dims:
        try:
            ttl = cache.ttl(_cooldown_key(dim, value)) if hasattr(cache, "ttl") else None
        except Exception:  # noqa: BLE001 缓存不支持 ttl 时退化为存在性判断
            ttl = None
        if ttl is None:
            try:
                ttl = 1 if cache.get(_cooldown_key(dim, value)) else 0
            except Exception:  # noqa: BLE001
                ttl = 0
        remaining = max(remaining, int(ttl or 0))
    return remaining


def register_failure(dims: list[tuple[str, str]]) -> dict[str, Any]:
    """记录一次失败并按阶梯返回处置：``force_hard`` / ``cooldown`` 秒。"""
    first, second, third = parse_steps()
    if not first:
        return {"force_hard": False, "cooldown": 0}
    highest = 0
    for dim, value in dims:
        try:
            key = _fail_key(dim, value)
            count = int(cache.get(key, 0) or 0) + 1
            cache.set(key, count, FAIL_WINDOW)
            highest = max(highest, count)
        except Exception:  # noqa: BLE001 计数不可用 = 不处置（不误伤）
            continue
    if highest >= third:
        _set_cooldown(dims, COOLDOWN_SECONDS["third"])
        return {"force_hard": True, "cooldown": COOLDOWN_SECONDS["third"]}
    if highest >= second:
        _set_cooldown(dims, COOLDOWN_SECONDS["second"])
        return {"force_hard": True, "cooldown": COOLDOWN_SECONDS["second"]}
    return {"force_hard": highest >= first, "cooldown": 0}


def forced_hard(dims: list[tuple[str, str]]) -> bool:
    """是否已到阶梯第一档：任一维度失败次数 ≥ 首档阈值（下一题强制难题型）。"""
    first, _second, _third = parse_steps()
    if not first:
        return False
    for dim, value in dims:
        try:
            if int(cache.get(_fail_key(dim, value), 0) or 0) >= first:
                return True
        except Exception:  # noqa: BLE001 计数不可用 = 不处置（不误伤）
            continue
    return False


def _set_cooldown(dims: list[tuple[str, str]], seconds: int) -> None:
    for dim, value in dims:
        try:
            cache.set(_cooldown_key(dim, value), 1, seconds)
        except Exception:  # noqa: BLE001
            continue


def clear_failures(dims: list[tuple[str, str]]) -> None:
    """验证通过：清空计数与冷却（阶梯只在连续失败时累积）。"""
    for dim, value in dims:
        try:
            cache.delete(_fail_key(dim, value))
            cache.delete(_cooldown_key(dim, value))
        except Exception:  # noqa: BLE001
            continue


def link_login_lock(username: str, ip: str) -> None:
    """可选联动：验证码失败达阈值时锁登录账号（默认关闭）。"""
    if not username or not getattr(settings, "CAPTCHA_FAILURE_LINK_LOCK", False):
        return
    try:
        from django.core.cache import cache

        from settings.services import LoginBlockUtil

        util = LoginBlockUtil(username, ip)
        cache.set(util.block_key, True, util.key_ttl)
    except Exception:  # noqa: BLE001 处置失败不影响主链路
        logger.debug("captcha link login lock failed", exc_info=True)
