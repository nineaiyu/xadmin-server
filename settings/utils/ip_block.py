#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录拦截 IP 的 Redis 伪 queryset 与 IP/整数互转工具。

ViewSet 只负责请求编排；列表过滤与批量删除共用的纯逻辑收敛于此，
便于单测覆盖（不启动 HTTP 栈）。
"""

import ipaddress
from typing import Any


class FilterIps(list[Any]):
    """Redis 键列表的列表视图：批量删除按 pk 反查真实 IP。"""

    def filter(self, pk__in: Any = None) -> list[Any]:
        if pk__in is None:
            pk__in = []
        return [obj.get("ip") for obj in self.__iter__() if obj.get("pk")() in pk__in]


class IpUtils:
    """IPv4/IPv6 与整数串互转（列表 pk 用整数串，避免 IP 中的点/冒号进入 URL 段）。"""

    def __init__(self, ip: str) -> None:
        self.ip = ip

    def ip_to_int(self) -> str:
        return str(int(ipaddress.ip_address(self.ip)))

    def int_to_ip(self) -> str:
        return str(ipaddress.ip_address(int(self.ip)))
