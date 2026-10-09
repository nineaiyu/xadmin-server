# -*- coding: utf-8 -*-
"""packages/xadmin-common/common/utils/random.py：随机串生成与字符替换边界。"""

import pytest

from common.utils.random import random_ip, random_replace_char, random_string, string_punctuation


class TestRandomReplaceChar:
    def test_replaces_requested_count_and_keeps_first_char(self):
        seq = list("abcdef")
        result = random_replace_char(seq, "!#", 2)
        assert result is seq
        assert sum(1 for ch in result[1:] if ch in "!#") == 2
        assert result[0] == "a"  # 首字符保留（与既有语义一致）

    def test_zero_length_is_noop(self):
        assert random_replace_char(list("abcdef"), "!", 0) == list("abcdef")

    def test_too_short_sequence_raises(self):
        """可用位置不足时显式报错。

        历史缺陷：`randbelow(len(seq) - 1)` 在短序列上恒取 0（跳过下标 0）造成死循环，
        len(seq) == 1 时 randbelow(0) 直接抛内部 ValueError。
        """
        with pytest.raises(ValueError):
            random_replace_char(list("ab"), "!", 3)


class TestRandomString:
    def test_special_char_included_for_normal_length(self):
        value = random_string(32, special_char=True)
        assert len(value) == 32
        assert any(ch in string_punctuation for ch in value)

    def test_short_length_with_special_char_raises(self):
        """长度不足以放特殊字符时显式失败，而不是永久空转（历史缺陷）。"""
        with pytest.raises(ValueError):
            random_string(4, special_char=True)

    def test_default_flags_require_length_at_least_four(self):
        with pytest.raises(ValueError):
            random_string(3)


class TestRandomIp:
    def test_generates_parsable_ipv4(self):
        import ipaddress

        assert ipaddress.ip_address(random_ip())
