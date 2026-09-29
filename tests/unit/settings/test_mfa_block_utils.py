# -*- coding: utf-8 -*-
"""MFA 防爆破计数：per-(用户, IP) 与 per-用户 双键，任一超限即锁。

单键（含 IP）计数可被轮换出口 IP 绕过——每个 IP 拿到独立额度，多 IP 并行即可
对有限空间（TOTP 6 位码）在线爆破；用户维度总闸堵死该面。
"""

import pytest

from settings.utils.security import LoginBlockUtil, MFABlockUtils

pytestmark = pytest.mark.django_db

LIMIT = 5


@pytest.fixture(autouse=True)
def block_settings(settings):
    settings.SECURITY_LOGIN_LIMIT_COUNT = LIMIT
    settings.SECURITY_LOGIN_LIMIT_TIME = 30
    return settings


def test_single_ip_limit_blocks(block_settings):
    block = MFABlockUtils("alice", "10.0.0.1")
    for _ in range(LIMIT):
        block.incr_failed_count()
    assert block.is_block() is True
    assert MFABlockUtils.is_user_block("alice") is True


def test_ip_rotation_cannot_bypass_user_limit(block_settings):
    """对抗性：轮换出口 IP（每 IP 各 1 次失败）累计到达上限即锁。"""
    for index in range(LIMIT - 1):
        MFABlockUtils("bob", f"10.0.0.{index}").incr_failed_count()
        assert MFABlockUtils("bob", "10.0.0.250").is_block() is False
    MFABlockUtils("bob", "10.0.0.9").incr_failed_count()
    # 用户维度已达上限：任意新 IP 的下一块也处于锁定态
    assert MFABlockUtils("bob", "10.0.0.250").is_block() is True


def test_remaining_times_uses_worst_counter(block_settings):
    for index in range(LIMIT - 1):
        MFABlockUtils("carol", f"10.0.1.{index}").incr_failed_count()
    assert MFABlockUtils("carol", "10.0.1.250").get_remainder_times() == 1


def test_other_utils_keep_single_key_semantics(block_settings):
    """登录/重置/验证码场景不受影响：未声明用户维度键时行为与改造前一致。"""
    assert LoginBlockUtil.USER_LIMIT_KEY_TMPL == ""
    block = LoginBlockUtil("dave", "10.0.0.1")
    assert block.user_limit_key == ""
    block.incr_failed_count()
    assert block.get_failed_count() == 1
    assert block.get_remainder_times() == LIMIT - 1


def test_clean_failed_count_resets_both_counters(block_settings):
    """验证通过后的清零：当前 (用户, IP) 计数与用户维度计数一并归零、解除锁定。

    其它 IP 的残留计数按 TTL 自然过期（与改造前一致，不在成功路径上扫通配键）。
    """
    for index in range(LIMIT):
        MFABlockUtils("erin", f"10.0.2.{index}").incr_failed_count()
    block = MFABlockUtils("erin", "10.0.2.1")
    assert block.is_block() is True

    block.clean_failed_count()
    assert MFABlockUtils.is_user_block("erin") is False
    assert block.get_failed_count() == 0


def test_unblock_user_clears_user_counter(block_settings):
    for index in range(LIMIT - 1):
        MFABlockUtils("frank", f"10.0.3.{index}").incr_failed_count()
    MFABlockUtils("frank", "10.0.3.250").incr_failed_count()
    assert MFABlockUtils.is_user_block("frank") is True

    MFABlockUtils.unblock_user("frank")
    assert MFABlockUtils.is_user_block("frank") is False
    # 计数清零：解锁后一次失败不会立刻再次锁定
    fresh = MFABlockUtils("frank", "10.0.3.1")
    fresh.incr_failed_count()
    assert fresh.is_block() is False
