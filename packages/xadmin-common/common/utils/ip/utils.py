import ipaddress
import socket
from ipaddress import ip_address, ip_network

from django.utils.translation import gettext_lazy as _

from common.settings_contract import kernel_required_setting

from .geoip import get_ip_city_by_geoip
from .ipip import get_ip_city_by_ipip


def is_ip_address(address):
    """192.168.10.1"""
    try:
        ip_address(address)
    except ValueError:
        return False
    else:
        return True


def is_ip_network(ip):
    """192.168.1.0/24"""
    try:
        ip_network(ip)
    except ValueError:
        return False
    else:
        return True


def is_ip_segment(ip):
    """10.1.1.1-10.1.1.20（区间两端同族且 start ≤ end）。

    保存期口径与登录策略网段校验一致：多 ``-``、倒置区间、跨协议族一律不合法；
    运行时对存量数据的容忍匹配见 contains_ip / in_ip_segment。
    """
    if "-" not in ip:
        return False
    parts = ip.split("-")
    if len(parts) != 2 or not (is_ip_address(parts[0]) and is_ip_address(parts[1])):
        return False
    start_ip, end_ip = ip_address(parts[0]), ip_address(parts[1])
    return type(start_ip) is type(end_ip) and int(start_ip) <= int(end_ip)


def _is_ip_range_pair(entry):
    """运行时宽松区间判定：两端为合法 IP 即可（容忍倒置，交由 in_ip_segment 归一）。

    与 is_ip_segment 的差异仅服务于运行时匹配——存量配置中可能存在收紧口径
    之前保存的倒置区间，min/max 匹配语义保持不变；保存期校验仍以 is_ip_segment 为准。
    """
    parts = entry.split("-") if isinstance(entry, str) else []
    return len(parts) == 2 and is_ip_address(parts[0]) and is_ip_address(parts[1])


def in_ip_segment(ip, ip_segment):
    ip1, ip2 = ip_segment.split("-")
    ip1 = int(ip_address(ip1))
    ip2 = int(ip_address(ip2))
    ip = int(ip_address(ip))
    return min(ip1, ip2) <= ip <= max(ip1, ip2)


def contains_ip(ip, ip_group):
    """
    ip_group:
    [192.168.10.1, 192.168.1.0/24, 10.1.1.1-10.1.1.20, 2001:db8:2de::e13, 2001:db8:1a:1110::/64.]

    """

    if "*" in ip_group:
        return True

    for _ip in ip_group:
        if is_ip_address(_ip):
            # 192.168.10.1
            if ip == _ip:
                return True
        elif is_ip_network(_ip) and is_ip_address(ip):
            # 192.168.1.0/24
            if ip_address(ip) in ip_network(_ip):
                return True
        elif _is_ip_range_pair(_ip) and is_ip_address(ip):
            # 10.1.1.1-10.1.1.20（运行时容忍倒置区间，匹配语义不变）
            if in_ip_segment(ip, _ip):
                return True
        else:
            # address / host
            if ip == _ip:
                return True

    return False


def is_ip(ip, rule_value):
    if rule_value == "*":
        return True
    elif "/" in rule_value:
        network = ipaddress.ip_network(rule_value)
        return ip in network.hosts()
    elif "-" in rule_value:
        start_ip, end_ip = rule_value.split("-")
        start_ip = ipaddress.ip_address(start_ip)
        end_ip = ipaddress.ip_address(end_ip)
        return start_ip <= ip <= end_ip
    elif len(rule_value.split(".")) == 4:
        return ip == rule_value
    else:
        return ip.startswith(rule_value)


def get_ip_city(ip):
    if not ip or not isinstance(ip, str):
        return _("Invalid address")
    if ":" in ip:
        return "IPv6"

    info = get_ip_city_by_ipip(ip)
    if info:
        city = info.get("city", None)
        country = info.get("country")

        # 国内城市 并且 语言是中文就使用国内
        is_zh = kernel_required_setting("LANGUAGE_CODE").startswith("zh")
        if country == "中国" and is_zh:
            return city if city else get_ip_city_by_geoip(ip)
    return get_ip_city_by_geoip(ip)


def lookup_domain(domain):
    try:
        return socket.gethostbyname(domain), ""
    except Exception as e:
        # 域名解析失败：返回可读原因（由调用方展示）
        return None, f"Cannot resolve {domain}: Unknown host, {e}"
