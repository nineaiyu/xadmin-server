# -*- coding: utf-8 -*-
"""出站请求地址守卫（SSRF / DNS rebinding）单元测试。

覆盖：协议白名单、地址归属（私网/环回/link-local/元数据/隧道/IPv4-mapped）、
域名解析（注入 resolver）、白名单放行、解析失败容错，以及固定解析连接
（Host 头保留域名、连接目标为已校验 IP；HTTPS 走自签证书验证 SNI/证书链路）。
"""

import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from common.utils.outbound import (
    OutboundBlocked,
    parse_allowed_hosts,
    pinned_request,
    resolve_outbound_target,
    validate_outbound_config_url,
    validate_outbound_url,
)

# 公网地址样本（避免真实 DNS 依赖）
PUBLIC_IP = "93.184.216.34"


def resolver_for(*ips):
    return lambda _host: list(ips)


class TestUrlGuard:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/1", "ftp://x/a", "//x/a"])
    def test_scheme_whitelist(self, url):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url(url)

    def test_missing_host(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("http:///path")

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://[::1]/x",
        ],
    )
    def test_loopback_requires_flag(self, url):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url(url, resolver=resolver_for("127.0.0.1"))
        assert validate_outbound_url(url, allow_loopback=True, resolver=resolver_for("127.0.0.1"))

    @pytest.mark.parametrize(
        "url,ip",
        [
            ("http://10.1.2.3/x", "10.1.2.3"),
            ("http://172.16.5.5/x", "172.16.5.5"),
            ("http://192.168.1.10/x", "192.168.1.10"),
            ("http://[fd00::1]/x", "fd00::1"),
        ],
    )
    def test_private_requires_flag(self, url, ip):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url(url)
        assert validate_outbound_url(url, allow_private=True)

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # 云元数据（link-local）
            "http://100.100.100.200/latest/meta-data/",  # 阿里云元数据
            "http://[fd00:ec2::254]/x",  # AWS IPv6 元数据
            "http://[fe80::1]/x",  # IPv6 link-local
            "http://[2002:0a00:0001::1]/x",  # 6to4（编码 10.0.1.1）
            "http://[2001:0000:4136:e378::1]/x",  # Teredo
            "http://0.0.0.0/x",
            "http://224.0.0.1/x",
        ],
    )
    def test_always_blocked_even_with_private_flag(self, url):
        for kwargs in ({}, {"allow_private": True}, {"allow_private": True, "allow_loopback": True}):
            with pytest.raises(OutboundBlocked):
                validate_outbound_url(url, **kwargs)

    def test_ipv4_mapped_ipv6_private_blocked(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("http://[::ffff:10.0.0.1]/x")

    def test_public_ip_allowed(self):
        target = validate_outbound_url(f"http://{PUBLIC_IP}/x")
        assert target.ips == (PUBLIC_IP,)
        assert target.is_ip_literal is True

    def test_domain_resolution_private_blocked(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("https://internal.corp/hook", resolver=resolver_for("10.0.0.5"))
        assert validate_outbound_url(
            "https://internal.corp/hook", allow_private=True, resolver=resolver_for("10.0.0.5")
        )

    def test_domain_resolution_metadata_blocked_with_private_flag(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_url(
                "https://evil.example.com/hook", allow_private=True, resolver=resolver_for("169.254.169.254")
            )

    def test_mixed_resolution_is_fail_closed(self):
        """解析出多个 IP（一个公网一个私网）时按拒绝处理。"""
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("https://mixed.example.com/x", resolver=resolver_for(PUBLIC_IP, "10.0.0.1"))

    def test_resolve_failure_strict_vs_tolerant(self):
        def failing(_host):
            raise OutboundBlocked("cannot resolve")

        with pytest.raises(OutboundBlocked):
            validate_outbound_url("https://not-ready.example.com/x", resolver=failing)
        # 写入侧容错模式：域名不做解析（发送侧仍会严格校验）
        assert validate_outbound_url("https://not-ready.example.com/x", resolver=failing, strict_resolve=False)

    def test_write_side_skips_domain_resolution_but_still_checks_literal(self):
        """写入侧口径：域名不解析（哪怕解析到私网），IP 字面量仍逐项校验。"""
        target = validate_outbound_url(
            "https://internal.corp/hook", resolver=resolver_for("10.0.0.5"), strict_resolve=False
        )
        assert target.ips == ()
        assert target.pinned_url == "https://internal.corp/hook"
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("http://10.0.0.5/hook", strict_resolve=False)

    def test_pinned_target_keeps_host_and_query(self):
        target = validate_outbound_url(
            "https://hooks.example.com:8443/a/b?x=1", allow_private=True, resolver=resolver_for(PUBLIC_IP)
        )
        assert target.pinned_url == f"https://{PUBLIC_IP}:8443/a/b?x=1"
        assert target.host_header == "hooks.example.com:8443"

    def test_pinned_target_default_port_omitted_in_host_header(self):
        target = validate_outbound_url("https://hooks.example.com/a", resolver=resolver_for(PUBLIC_IP))
        assert target.host_header == "hooks.example.com"
        assert target.pinned_url == f"https://{PUBLIC_IP}/a"


class TestWhitelist:
    def test_whitelist_skips_resolution_and_private_check(self):
        target = validate_outbound_url(
            "https://internal.corp/hook",
            allowed_hosts=parse_allowed_hosts("internal.corp, 10.0.0.5"),
            resolver=resolver_for("10.0.0.5"),
        )
        assert target.pinned_url == "https://internal.corp/hook"
        assert target.ips == ()

    def test_whitelist_still_blocks_metadata_when_listed(self):
        """白名单显式登记 link-local/元数据同样拒绝（避免误配造成元数据泄露）。"""
        with pytest.raises(OutboundBlocked):
            validate_outbound_url("http://169.254.169.254/x", allowed_hosts=("169.254.169.254",))

    def test_parse_allowed_hosts_normalizes(self):
        assert parse_allowed_hosts(" A.example.com ,\nB.example.com:8443 ,[::1]:8000,") == (
            "a.example.com",
            "b.example.com",
            "::1",
        )


class TestValidateOutboundConfigUrl:
    """写入侧出站配置地址统一校验（Webhook / AI base_url / MCP / 回调地址共用）。"""

    def test_https_allowed(self):
        url = "https://api.example.com/v1"
        assert validate_outbound_config_url(url) == url

    def test_missing_host_rejected(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url("https://")

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/1", "ftp://api.example.com/v1", "not-a-url", ""])
    def test_non_http_scheme_rejected(self, url):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url(url)

    @pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:11434/v1"])
    def test_loopback_http_allowed(self, url):
        assert validate_outbound_config_url(url) == url

    def test_loopback_http_rejected_when_disabled(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url("http://127.0.0.1/v1", allow_loopback=False)

    def test_whitelisted_http_allowed(self):
        url = "http://mcp.internal:8765/mcp"
        assert validate_outbound_config_url(url, allowed_hosts=("mcp.internal",)) == url

    def test_whitelisted_http_rejected_when_not_allowed(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url(
                "http://hooks.internal/hook",
                allowed_hosts=("hooks.internal",),
                allow_http_whitelist=False,
            )

    def test_https_private_literal_rejected(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url("https://10.0.0.8/v1")

    def test_http_private_literal_rejected(self):
        with pytest.raises(OutboundBlocked):
            validate_outbound_config_url("http://192.168.1.10:11434/v1")

    def test_scheme_message_override(self):
        with pytest.raises(OutboundBlocked) as exc:
            validate_outbound_config_url("ftp://x", scheme_message="custom message")
        assert "custom message" in str(exc.value)


class _EchoHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).received.append({"host": self.headers.get("Host"), "path": self.path})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    _EchoHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def https_server(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl 不可用")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=pinned.test",
            "-addext",
            "subjectAltName=DNS:pinned.test",
        ],
        check=True,
        capture_output=True,
    )
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, str(cert)
    server.shutdown()


class TestPinnedRequest:
    def test_pinned_request_uses_ip_with_original_host(self, http_server):
        _server, netloc = http_server
        port = netloc.split(":")[1]
        response = pinned_request(
            "POST",
            f"http://pinned.test:{port}/hook?x=1",
            allow_loopback=True,
            resolver=resolver_for("127.0.0.1"),
            data=b"{}",
            timeout=5,
        )
        assert response.status_code == 200
        assert _EchoHandler.received == [{"host": f"pinned.test:{port}", "path": "/hook?x=1"}]

    def test_pinned_request_rejects_blocked_target(self, http_server):
        _server, netloc = http_server
        port = netloc.split(":")[1]
        with pytest.raises(OutboundBlocked):
            pinned_request(
                "POST",
                f"http://blocked.test:{port}/hook",
                allow_private=False,
                allow_loopback=False,
                resolver=resolver_for("127.0.0.1"),
                data=b"{}",
                timeout=5,
            )
        assert _EchoHandler.received == []

    def test_pinned_request_https_sni_and_cert_by_domain(self, https_server):
        """HTTPS：连接目标是 IP，但 SNI/证书校验按域名（自签 CA 校验通过）。"""
        server, cert_path = https_server
        port = server.server_address[1]
        response = pinned_request(
            "POST",
            f"https://pinned.test:{port}/hook",
            allow_loopback=True,
            resolver=resolver_for("127.0.0.1"),
            data=b"{}",
            verify=cert_path,
            timeout=5,
        )
        assert response.status_code == 200
        assert _EchoHandler.received[-1]["host"] == f"pinned.test:{port}"


class TestResolveTargetInternals:
    def test_ip_literal_keeps_original_url(self):
        target = resolve_outbound_target("http://8.8.8.8/x")
        assert target.is_ip_literal is True
        assert target.pinned_url == "http://8.8.8.8/x"

    def test_resolver_error_is_fail_closed(self):
        def boom(_host):
            raise RuntimeError("dns down")

        with pytest.raises(RuntimeError):
            resolve_outbound_target("https://x.example.com/a", resolver=boom)
