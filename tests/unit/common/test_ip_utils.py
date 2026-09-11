# -*- coding: utf-8 -*-
"""common/utils/ip/utils.py 纯函数回归：IP/网段/区间判定与归属地兜底链路。

is_ip 的 `/` 与 `-` 分支比较的是 ip_address 对象（调用方传入前需自行转换），
`.` 四段与前缀分支比较的是字符串——测试按真实入参类型分别驱动，锁住语义。
"""

from unittest import mock

import pytest
from django.utils.translation import activate
from ipaddress import ip_address

from common.utils.ip.utils import (
    contains_ip,
    get_ip_city,
    in_ip_segment,
    is_ip,
    is_ip_address,
    is_ip_network,
    is_ip_segment,
    lookup_domain,
)


class TestPredicates:
    def test_is_ip_address(self):
        assert is_ip_address("192.168.10.1") is True
        assert is_ip_address("2001:db8::1") is True
        assert is_ip_address("999.1.1.1") is False
        assert is_ip_address("not-an-ip") is False

    def test_is_ip_network(self):
        assert is_ip_network("192.168.1.0/24") is True
        assert is_ip_network("192.168.1.0/33") is False
        assert is_ip_network("192.168.1.1") is True  # 裸地址按 /32 网络解析（ip_network 语义）

    def test_is_ip_segment(self):
        assert is_ip_segment("10.1.1.1-10.1.1.20") is True
        assert is_ip_segment("10.1.1.20-10.1.1.1") is True
        assert is_ip_segment("10.1.1.1") is False  # 无 "-"
        assert is_ip_segment("abc-def") is False

    def test_in_ip_segment(self):
        assert in_ip_segment("10.1.1.5", "10.1.1.1-10.1.1.20") is True
        assert in_ip_segment("10.1.1.25", "10.1.1.1-10.1.1.20") is False
        # 端点倒序同样生效（min/max 归一）
        assert in_ip_segment("10.1.1.5", "10.1.1.20-10.1.1.1") is True


class TestContainsIp:
    def test_wildcard_group(self):
        assert contains_ip("8.8.8.8", "*") is True

    def test_exact_address(self):
        assert contains_ip("192.168.10.1", ["192.168.10.1"]) is True
        assert contains_ip("192.168.10.2", ["192.168.10.1"]) is False

    def test_network(self):
        assert contains_ip("192.168.1.66", ["192.168.1.0/24"]) is True
        assert contains_ip("192.168.2.66", ["192.168.1.0/24"]) is False

    def test_segment(self):
        assert contains_ip("10.1.1.15", ["10.1.1.1-10.1.1.20"]) is True
        assert contains_ip("10.1.1.21", ["10.1.1.1-10.1.1.20"]) is False

    def test_hostname_fallback_equal_or_miss(self):
        # 非 IP 形态按字符串等值兜底（address / host）
        assert contains_ip("example.com", ["example.com"]) is True
        assert contains_ip("example.com", ["example.org"]) is False

    def test_ipv6(self):
        assert contains_ip("2001:db8:2de::e13", ["2001:db8:2de::e13"]) is True
        assert contains_ip("2001:db8:1a:1110::1", ["2001:db8:1a:1110::/64"]) is True


class TestIsIp:
    def test_wildcard(self):
        assert is_ip(ip_address("1.2.3.4"), "*") is True

    def test_cidr(self):
        assert is_ip(ip_address("10.1.2.3"), "10.0.0.0/8") is True
        assert is_ip(ip_address("11.1.2.3"), "10.0.0.0/8") is False

    def test_range(self):
        assert is_ip(ip_address("10.1.1.5"), "10.1.1.1-10.1.1.20") is True
        assert is_ip(ip_address("10.1.1.25"), "10.1.1.1-10.1.1.20") is False

    def test_quad_exact(self):
        assert is_ip("192.168.10.1", "192.168.10.1") is True
        assert is_ip("192.168.10.1", "192.168.10.2") is False

    def test_prefix_match(self):
        # 非 IP 形态走 startswith 前缀匹配（ip 入参为字符串）
        assert is_ip("192.168", "192.16") is True
        assert is_ip("192.168", "10.0") is False


class TestGetIpCity:
    @pytest.fixture(autouse=True)
    def _zh(self, settings):
        settings.LANGUAGE_CODE = "zh-hans"
        activate("zh-hans")

    def test_invalid_input(self):
        # 返回值是 gettext 惰性代理：断言语义（无效地址）而非具体文案
        assert "Invalid" in str(get_ip_city("")) or "地址" in str(get_ip_city(""))
        assert str(get_ip_city(None)) == str(get_ip_city(""))
        assert str(get_ip_city(123)) == str(get_ip_city(""))

    def test_ipv6_short_circuit(self):
        assert get_ip_city("2001:db8::1") == "IPv6"

    @mock.patch("common.utils.ip.utils.get_ip_city_by_geoip")
    @mock.patch("common.utils.ip.utils.get_ip_city_by_ipip")
    def test_china_and_zh_uses_city(self, m_ipip, m_geoip):
        m_ipip.return_value = {"country": "中国", "city": "杭州"}
        assert get_ip_city("223.5.5.5") == "杭州"

    @mock.patch("common.utils.ip.utils.get_ip_city_by_geoip")
    @mock.patch("common.utils.ip.utils.get_ip_city_by_ipip")
    def test_china_without_city_falls_back_geoip(self, m_ipip, m_geoip):
        m_ipip.return_value = {"country": "中国", "city": None}
        m_geoip.return_value = "上海"
        assert get_ip_city("223.5.5.5") == "上海"

    @mock.patch("common.utils.ip.utils.get_ip_city_by_geoip")
    @mock.patch("common.utils.ip.utils.get_ip_city_by_ipip")
    def test_foreign_uses_geoip(self, m_ipip, m_geoip):
        m_ipip.return_value = {"country": "美国", "city": "NY"}
        m_geoip.return_value = "洛杉矶"
        assert get_ip_city("8.8.8.8") == "洛杉矶"

    @mock.patch("common.utils.ip.utils.get_ip_city_by_geoip")
    @mock.patch("common.utils.ip.utils.get_ip_city_by_ipip")
    def test_ipip_empty_falls_back_geoip(self, m_ipip, m_geoip):
        m_ipip.return_value = None
        m_geoip.return_value = "北京"
        assert get_ip_city("1.2.3.4") == "北京"


class TestLookupDomain:
    @mock.patch("common.utils.ip.utils.socket.gethostbyname", return_value="93.184.216.34")
    def test_resolved(self, _m):
        ip, err = lookup_domain("example.com")
        assert ip == "93.184.216.34"
        assert err == ""

    @mock.patch(
        "common.utils.ip.utils.socket.gethostbyname",
        side_effect=OSError("NXDOMAIN"),
    )
    def test_unresolved(self, _m):
        ip, err = lookup_domain("no.such.host")
        assert ip is None
        assert "Cannot resolve no.such.host" in err
