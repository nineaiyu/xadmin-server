#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""IP 白名单清洗（PAT 与开放平台应用共用唯一实现）。

两处序列化器此前各自实现：PAT 版校验 IP/CIDR 格式，应用版只做 strip+过滤，
非法条目静默落库后「白名单」实际不生效（或空清单被误读为不限）。格式校验与
清洗口径必须单点收口，避免同一字段在不同凭证类型下语义漂移。
"""

import ipaddress
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


def clean_ip_allowlist(value: Any) -> list[str]:
    """去空白 / 去重 / 逐条校验 IP 或 CIDR；None/空 = 不限（空列表）。"""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise serializers.ValidationError(_("Ip allowlist must be a list of IPs or CIDR networks"))
    cleaned: list[str] = []
    for item in value:
        entry = str(item or "").strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                ipaddress.ip_network(entry, strict=False)
            else:
                ipaddress.ip_address(entry)
        except ValueError as exc:
            raise serializers.ValidationError(_("Invalid IP or CIDR network: %(entry)s") % {"entry": entry}) from exc
        if entry not in cleaned:
            cleaned.append(entry)
    return cleaned
