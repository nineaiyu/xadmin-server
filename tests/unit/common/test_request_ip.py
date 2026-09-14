# -*- coding: utf-8 -*-
"""get_request_ip 防伪造测试（S1：XFF 信任策略）。

口径：只有直连地址命中 TRUSTED_PROXY_IPS 时才解析 X-Forwarded-For，
否则一律使用直连地址，攻击者伪造 XFF 无法影响 IP 登录封禁 / PAT IP 白名单。
"""

import pytest
from django.test import RequestFactory

from common.utils.request import get_request_ip


@pytest.fixture
def rf():
    return RequestFactory()


def make_request(rf, remote_addr, xff=None):
    extra = {"REMOTE_ADDR": remote_addr} if remote_addr else {}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return rf.get("/api/demo/book", **extra)


class TestClientIpWithoutTrustedProxy:
    def test_no_xff_uses_remote_addr(self, rf):
        request = make_request(rf, "10.1.2.3")
        assert get_request_ip(request) == "10.1.2.3"

    def test_forged_xff_ignored_by_default(self, rf, settings):
        """默认空清单：伪造 XFF 首值不再生效（旧实现取首值即此绕过点）。"""
        settings.TRUSTED_PROXY_IPS = []
        request = make_request(rf, "10.1.2.3", "203.0.113.9")
        assert get_request_ip(request) == "10.1.2.3"

    def test_untrusted_source_cannot_inject_xff_chain(self, rf, settings):
        settings.TRUSTED_PROXY_IPS = ["192.168.196.0/24"]
        request = make_request(rf, "10.1.2.3", "192.168.196.5, 203.0.113.9")
        assert get_request_ip(request) == "10.1.2.3"

    def test_missing_remote_addr_falls_back_to_unknown(self, rf):
        request = make_request(rf, "")
        # RequestFactory 默认填充 127.0.0.1，显式模拟缺失（如内部调用无 META 场景）
        request.META["REMOTE_ADDR"] = ""
        assert get_request_ip(request) == "unknown"


class TestClientIpWithTrustedProxy:
    def test_rightmost_untrusted_address_wins(self, rf, settings):
        """可信反代 + 客户端伪造前缀：取链尾（右起第一个非可信）真实地址。"""
        settings.TRUSTED_PROXY_IPS = ["192.168.196.0/24"]
        request = make_request(rf, "192.168.196.2", "203.0.113.9, 198.51.100.7")
        assert get_request_ip(request) == "198.51.100.7"

    def test_single_hop_proxy(self, rf, settings):
        settings.TRUSTED_PROXY_IPS = ["192.168.196.2"]
        request = make_request(rf, "192.168.196.2", "198.51.100.7")
        assert get_request_ip(request) == "198.51.100.7"

    def test_all_trusted_chain_falls_back_to_remote_addr(self, rf, settings):
        settings.TRUSTED_PROXY_IPS = ["192.168.196.0/24"]
        request = make_request(rf, "192.168.196.2", "192.168.196.3, 192.168.196.4")
        assert get_request_ip(request) == "192.168.196.2"

    def test_invalid_cidr_entries_are_ignored(self, rf, settings):
        settings.TRUSTED_PROXY_IPS = ["not-a-cidr", "192.168.196.0/24"]
        request = make_request(rf, "192.168.196.2", "198.51.100.7")
        assert get_request_ip(request) == "198.51.100.7"


class TestNormalize:
    def test_ipv4_port_suffix_stripped(self, rf):
        request = make_request(rf, "10.1.2.3:54321")
        assert get_request_ip(request) == "10.1.2.3"

    def test_xff_whitespace_and_quotes_stripped(self, rf, settings):
        settings.TRUSTED_PROXY_IPS = ["192.168.196.0/24"]
        request = make_request(rf, "192.168.196.2", ' "198.51.100.7" , 192.168.196.2 ')
        assert get_request_ip(request) == "198.51.100.7"

    def test_ipv6_literal_kept(self, rf):
        request = make_request(rf, "2001:db8::1")
        assert get_request_ip(request) == "2001:db8::1"
