#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站请求目标安全校验与固定解析连接（SSRF / DNS rebinding 防护）。

适用对象：服务端主动发起、目标由管理面配置的请求（Webhook 投递、AI 服务地址）。

防线：

1. **协议白名单**：仅 http / https（拒绝 file://、gopher:// 等）；
2. **地址归属校验**：域名解析出的全部 IP 逐一检查——link-local（含云元数据
   169.254.169.254 / fd00:ec2::254）、multicast、reserved、unspecified 与隧道
   地址（6to4 / Teredo）在任何模式下都拒绝；私网与环回按调用方场景放行
   （内网自建服务、本地联调）；
3. **白名单**：``allowed_hosts`` 命中的目标按显式授权处理——是私网目标的
   唯一放行途径（跳过解析与私网判定，仍拒绝 link-local/元数据）；
4. **固定解析连接**：``pinned_request`` 先解析校验、再把连接目标固定为已校验
   IP（Host 头与 TLS SNI 保留原域名），消除「校验一次解析、连接又解析一次」的
   DNS rebinding 窗口。

用法::

    from common.utils.outbound import validate_outbound_url, pinned_request

    validate_outbound_url(url, allow_loopback=True)          # 仅校验（写入侧）
    response = pinned_request("POST", url, data=body, timeout=10)  # 校验 + 固定连接
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from requests.adapters import HTTPAdapter

# 域名解析器签名：host -> IP 字符串列表（默认真实 DNS；测试注入以离线断言）
Resolver = Callable[[str], Iterable[str]]

# 任何模式都拒绝的网段：无正常业务用途、且是 SSRF 的高危目标
ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local（AWS/GCP 等元数据在 169.254.169.254）
    ipaddress.ip_network("100.100.100.200/32"),  # 阿里云元数据（不在 link-local 段）
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IPv6 元数据
    ipaddress.ip_network("2002::/16"),  # 6to4 隧道（可编码内网 IPv4）
    ipaddress.ip_network("2001::/32"),  # Teredo 隧道
)


class OutboundBlocked(ValidationError):
    """出站目标被安全策略拒绝（ValidationError 子类，复用 DRF 400 语义）。"""


class OutboundTarget(NamedTuple):
    """校验后的出站目标：原 URL / 固定 IP 连接 URL / Host 头 / 解析结果。"""

    url: str
    scheme: str
    host: str
    port: int | None
    host_header: str
    ips: tuple[str, ...]
    pinned_url: str
    is_ip_literal: bool


def _unwrap(ip: Any) -> Any:
    """IPv4-mapped IPv6 还原为 IPv4，避免 ``::ffff:10.0.0.1`` 绕过私网判定。"""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _ensure_allowed(addr: Any, *, host: str, allow_private: bool, allow_loopback: bool) -> None:
    # loopback 必须先判：IPv6 的 ::1 落在 ipaddress 的 reserved 集合（::/8）内，
    # 先判 reserved 会把环回误判为「任何模式都拒绝」
    if addr.is_loopback:
        if not allow_loopback:
            raise OutboundBlocked(
                _("Outbound target must not be a loopback address (allow_loopback is off): {}").format(host)
            )
        return
    if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        raise OutboundBlocked(_("Outbound target address is not allowed: {}").format(host))
    for network in ALWAYS_BLOCKED_NETWORKS:
        if addr.version == network.version and addr in network:
            raise OutboundBlocked(_("Outbound target address is not allowed: {}").format(host))
    if addr.is_link_local:
        raise OutboundBlocked(_("Outbound target address is not allowed: {}").format(host))
    if addr.is_private or not addr.is_global:
        if not allow_private:
            raise OutboundBlocked(_("Outbound target must not be a private address: {}").format(host))


def resolve_host_ips(host: str, *, resolver: Resolver | None = None) -> tuple[str, ...]:
    """解析主机名 → 去重后的 IP 列表；解析失败按拒绝处理（fail-closed）。"""
    if resolver is not None:
        candidates = [str(item) for item in resolver(host)]
    else:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise OutboundBlocked(_("Outbound target domain cannot be resolved: {}").format(host)) from exc
        candidates = [str(info[4][0]) for info in infos]

    ips: list[str] = []
    for candidate in candidates:
        # IPv6 链路本地字面量可能带 scope（fe80::1%eth0），scope 不参与地址判定
        normalized = str(candidate).split("%")[0]
        try:
            ipaddress.ip_address(normalized)
        except ValueError as exc:
            raise OutboundBlocked(_("Outbound target resolved to an invalid address: {}").format(host)) from exc
        if normalized not in ips:
            ips.append(normalized)
    if not ips:
        raise OutboundBlocked(_("Outbound target domain cannot be resolved: {}").format(host))
    return tuple(ips)


def _pinned_url(parts: Any, scheme: str, pinned_host: str) -> str:
    host = pinned_host if ":" not in pinned_host else f"[{pinned_host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return str(urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment)))


def resolve_outbound_target(
    url: str,
    *,
    allow_private: bool = False,
    allow_loopback: bool = False,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver | None = None,
    strict_resolve: bool = True,
) -> OutboundTarget:
    """校验出站 URL 并计算固定解析连接所需的 URL / Host 头。

    :param allow_private: 是否允许私网目标（内网自建服务场景按需开启）
    :param allow_loopback: 是否允许环回目标（本地联调场景按需开启）
    :param allowed_hosts: 域名/IP 白名单（命中 = 显式授权：跳过地址校验）
    :param resolver: 域名解析器注入点（默认系统 DNS）
    :param strict_resolve: 严格解析模式（执行侧）。False 供写入侧使用：域名一律不做
        解析与归属判定（域名可能尚未上线、仅内网 DNS 可见，或被本地 DNS 屏蔽成
        0.0.0.0），归属校验统一由执行侧（发送时）承担；IP 字面量两种模式都校验
    """
    raw = str(url or "").strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise OutboundBlocked(_("Only http/https outbound urls are allowed"))
    host = str(parts.hostname or "").lower()
    if not host:
        raise OutboundBlocked(_("Outbound url must contain a host"))

    whitelist = {str(item).strip().lower() for item in (allowed_hosts or ()) if str(item).strip()}
    if host in whitelist:
        # 白名单 = 显式授权：域名不做解析（内网域名可能无法从本机解析）、按原 URL 请求；
        # IP 字面量仍过一遍「任何模式都拒绝」的地址集合（误配元数据地址不放行）
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            _ensure_allowed(literal, host=host, allow_private=True, allow_loopback=True)
        return OutboundTarget(
            url=raw,
            scheme=scheme,
            host=host,
            port=parts.port,
            host_header=parts.netloc,
            ips=(),
            pinned_url=raw,
            is_ip_literal=False,
        )

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        is_ip_literal = False
        if not strict_resolve:
            # 写入侧（配置态）：域名不做解析与归属判定——发送时由执行侧严格解析校验
            return OutboundTarget(
                url=raw,
                scheme=scheme,
                host=host,
                port=parts.port,
                host_header=parts.netloc,
                ips=(),
                pinned_url=raw,
                is_ip_literal=False,
            )
        ips = resolve_host_ips(host, resolver=resolver)
    else:
        is_ip_literal = True
        ips = (str(ip),)

    for candidate in ips:
        _ensure_allowed(
            ipaddress.ip_address(candidate), host=host, allow_private=allow_private, allow_loopback=allow_loopback
        )

    if is_ip_literal:
        return OutboundTarget(
            url=raw,
            scheme=scheme,
            host=host,
            port=parts.port,
            host_header=parts.netloc,
            ips=ips,
            pinned_url=raw,
            is_ip_literal=True,
        )

    default_port = 443 if scheme == "https" else 80
    host_header = host if parts.port in (None, default_port) else f"{host}:{parts.port}"
    return OutboundTarget(
        url=raw,
        scheme=scheme,
        host=host,
        port=parts.port,
        host_header=host_header,
        ips=ips,
        pinned_url=_pinned_url(parts, scheme, ips[0]),
        is_ip_literal=False,
    )


def validate_outbound_url(
    url: str,
    *,
    allow_private: bool = False,
    allow_loopback: bool = False,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver | None = None,
    strict_resolve: bool = True,
) -> OutboundTarget:
    """仅校验（不发起请求）：通过返回校验结果，拒绝抛 ``OutboundBlocked``。"""
    return resolve_outbound_target(
        url,
        allow_private=allow_private,
        allow_loopback=allow_loopback,
        allowed_hosts=allowed_hosts,
        resolver=resolver,
        strict_resolve=strict_resolve,
    )


class PinnedHostAdapter(HTTPAdapter):
    """HTTPS 连接固定：URL 用已校验 IP，TLS SNI 与证书校验保留原域名。

    urllib3 的 ``server_hostname`` / ``assert_hostname`` 经连接池 kwargs 下传：
    - server_hostname：握手 SNI 用域名（虚拟主机/证书匹配正确）；
    - assert_hostname：证书按域名校验（连接目标是 IP 也不影响）。
    """

    def __init__(self, hostname: str, **kwargs: Any) -> None:
        self._pinned_hostname = hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections: Any, maxsize: Any, block: bool = False, **pool_kwargs: Any) -> Any:
        pool_kwargs["server_hostname"] = self._pinned_hostname
        pool_kwargs["assert_hostname"] = self._pinned_hostname
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)


def pinned_request(
    method: str,
    url: str,
    *,
    allow_private: bool = False,
    allow_loopback: bool = False,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver | None = None,
    headers: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """校验后发起请求：域名目标固定为已校验 IP 连接（Host/SNI 保留域名）。

    IP 字面量目标无 DNS 解析环节，直接按原 URL 请求（Host 头交给 requests）。
    """
    import requests

    target = resolve_outbound_target(
        url,
        allow_private=allow_private,
        allow_loopback=allow_loopback,
        allowed_hosts=allowed_hosts,
        resolver=resolver,
    )
    request_url = target.url
    merged_headers = dict(headers or {})
    session = requests.Session()
    if not target.is_ip_literal:
        request_url = target.pinned_url
        merged_headers.setdefault("Host", target.host_header)
        if target.scheme == "https":
            session.mount("https://", PinnedHostAdapter(target.host))
    # 不主动 close session：调用方可能以 stream=True 消费响应体，关闭会截断流；
    # Session 生命周期随请求对象回收，连接池由 urllib3 自行清理
    return session.request(method, request_url, headers=merged_headers, **kwargs)


def parse_allowed_hosts(value: Any) -> tuple[str, ...]:
    """解析逗号/换行分隔的白名单配置（域名或 IP，大小写不敏感，兼容 host:port 写法）。"""
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").replace("\n", ",").split(",")
    hosts: list[str] = []
    for item in items:
        host = str(item).strip().lower()
        if not host:
            continue
        if host.startswith("["):  # [ipv6]:port
            end = host.find("]")
            if end > 0:
                host = host[1:end]
        elif host.count(":") == 1 and "." in host:  # host:port（IPv6 字面量不含 "."）
            host = host.split(":", 1)[0]
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


# 出站配置写入侧的 loopback 主机：http 仅对本地联调目标放行（https 不受限）
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def outbound_allowed_hosts() -> tuple[str, ...]:
    """出站白名单统一读取：系统配置 ``OUTBOUND_ALLOWED_HOSTS``（解析为域名/IP 元组）。

    各出站目标（Webhook / AI base_url / MCP server）同源消费本函数，配置不可用
    时按空白名单降级——私网目标即默认拒绝（fail-closed），不因配置读取异常放行。
    """
    try:
        from common.core.config import SysConfig

        return parse_allowed_hosts(SysConfig.OUTBOUND_ALLOWED_HOSTS)
    except Exception:  # noqa: BLE001 配置不可用时按空白名单（私网目标默认拒绝）
        return ()


def validate_outbound_config_url(
    url: str,
    *,
    allowed_hosts: Iterable[str] = (),
    allow_loopback: bool = True,
    allow_private: bool = False,
    allow_http_whitelist: bool = True,
    scheme_message: Any = None,
    resolver: Resolver | None = None,
) -> str:
    """写入侧出站配置地址统一校验（Webhook / AI base_url / MCP / 回调地址共用）。

    协议口径：https 一律放行；http 仅当目标为 loopback（本地联调）或
    ``allowed_hosts`` 白名单登记（内网自建服务）时放行——其余协议与未登记目标拒绝。

    与执行侧 ``pinned_request`` 同源：写入侧域名不做解析（可能尚未上线 / 仅内网
    DNS 可见 / 被本地 DNS 屏蔽），归属校验统一留到发送侧；IP 字面量两种模式都校验。

    :param allow_http_whitelist: http 是否接受白名单登记的目标（False = 仅 loopback）
    :param scheme_message: 协议不合规时的错误文案（各资源保留各自语义）
    """
    raw = str(url or "").strip()
    host = str(urlsplit(raw).hostname or "").lower()
    allowed = raw.startswith("https://")
    if not allowed and raw.startswith("http://"):
        if allow_loopback and host in LOOPBACK_HOSTS:
            allowed = True
        elif allow_http_whitelist:
            allowed = host in {str(item).strip().lower() for item in (allowed_hosts or ()) if str(item).strip()}
    if not allowed:
        raise OutboundBlocked(
            scheme_message or _("Outbound url must use https (http is allowed for loopback or registered targets)")
        )
    validate_outbound_url(
        raw,
        allow_private=allow_private,
        allow_loopback=allow_loopback,
        allowed_hosts=allowed_hosts,
        resolver=resolver,
        strict_resolve=False,
    )
    return raw
