# -*- coding: utf-8 -*-
"""IP 组条目校验：basic 页 ip_group_child_validator 与登录策略网段校验共用判定面。

两处保存期校验共享 common 层 is_ip_address / is_ip_network / is_ip_segment：
单 IP（IPv4/IPv6）/ 严格 CIDR / `a-b` 同族有序区间 / `*` 合法；多 `-`、倒置
区间、域名、乱串拒绝并回显条目原文。运行时 in_ip_segment 的 min/max 容忍
匹配不受保存期收紧影响（见 tests/unit/common/test_ip_utils.py）。
"""

import pytest
from django.utils.translation import activate
from rest_framework.exceptions import ValidationError

from identity.serializers.security import validate_login_policy_ip_ranges  # noqa: PLC2701
from settings.serializers.security import ip_group_child_validator


class TestIpGroupChildValidator:
    @pytest.fixture(autouse=True)
    def _en(self):
        activate("en")

    @pytest.mark.parametrize(
        "entry",
        [
            "192.168.10.1",
            "2001:db8:2de::e13",
            "192.168.1.0/24",
            "2001:db8:1a:1110::/64",
            "10.1.1.1-10.1.1.20",
            "*",
        ],
    )
    def test_valid_entries_pass(self, entry):
        assert ip_group_child_validator(entry) is None

    @pytest.mark.parametrize(
        "entry",
        [
            "1.1.1.1-2.2.2.2-3",  # 多 "-"：不再解包崩 500
            "2.2.2.2-1.1.1.1",  # 倒置区间
            "1.1.1.1-2001:db8::1",  # 跨协议族
            "example.com",  # 域名
            "1.2.3.256",  # 乱串 / 越界
        ],
    )
    def test_invalid_entries_rejected_with_entry_echo(self, entry):
        with pytest.raises(ValidationError) as exc:
            ip_group_child_validator(entry)
        assert entry in str(exc.value)


class TestLoginPolicyIpRanges:
    """登录策略网段校验：与 basic 页共用判定，保留区间形态的精细化提示。"""

    @pytest.fixture(autouse=True)
    def _en(self):
        activate("en")

    def test_valid_multiline_pass(self):
        validate_login_policy_ip_ranges("192.168.1.0/24\n10.1.1.1-10.1.1.20\n*\n2001:db8::1")

    def test_inverted_range_specific_message(self):
        with pytest.raises(ValidationError) as exc:
            validate_login_policy_ip_ranges("2.2.2.2-1.1.1.1")
        assert "must not be greater than the end IP" in str(exc.value)
        assert "2.2.2.2-1.1.1.1" in str(exc.value)

    def test_mixed_family_specific_message(self):
        with pytest.raises(ValidationError) as exc:
            validate_login_policy_ip_ranges("1.1.1.1-2001:db8::1")
        assert "same IP version" in str(exc.value)

    @pytest.mark.parametrize("entry", ["1.1.1.1-2.2.2.2-3", "example.com", "not an ip"])
    def test_invalid_shape_general_message(self, entry):
        with pytest.raises(ValidationError) as exc:
            validate_login_policy_ip_ranges(entry)
        assert "each line must be" in str(exc.value)
        assert entry in str(exc.value)
