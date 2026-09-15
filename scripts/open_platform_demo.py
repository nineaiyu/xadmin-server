#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开放平台接入示例（第三方视角的端到端演示，ADR-039 出口交付物）。

依赖：仅标准库 + requests（`pip install requests`）。

用法：
    # client-credentials 全流程（换发 → 调用 → 限流观测）
    python scripts/open_platform_demo.py --base-url http://127.0.0.1:8896 \\
        --client-id app_xxx --client-secret aps_xxx

    # 追加 OAuth 授权码演示（需要 xadmin 用户账号，走同意页 API）
    python scripts/open_platform_demo.py --base-url http://127.0.0.1:8896 \\
        --client-id app_xxx --client-secret aps_xxx \\
        --oauth --username admin --password 'Admin@123456'

    # 追加 webhook 验签演示（本地起接收端；需在「Webhook 订阅」页把 URL 登记为
    # http://127.0.0.1:18898/hook 并订阅 webhook.ping，然后在订阅页点「测试」）
    python scripts/open_platform_demo.py --receive-webhook

说明：
- 本脚本模拟第三方客户端：只使用 HTTP API，不依赖 Django；
- 演示了「应用 scope（接口）× 应用资源授权（模型×动作×字段×行）× 用户权限」
  三层收敛语义与 429 限流响应。
"""

import argparse
import hashlib
import hmac
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import requests
except ImportError:  # pragma: no cover
    print("请先安装 requests：pip install requests")
    sys.exit(2)


def step(title):
    print(f"\n=== {title} ===")


def show(resp, expect_json=True):
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:300]
    print(f"HTTP {resp.status_code}: {json.dumps(body, ensure_ascii=False)[:400] if expect_json else body}")
    return body


def issue_token(base_url, client_id, client_secret):
    """client-credentials 换发（轮换语义：旧凭证即时失效）。"""
    step("1. 换发凭证（POST /api/system/open/token）")
    resp = requests.post(
        f"{base_url}/api/system/open/token",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=10,
    )
    body = show(resp)
    if resp.status_code != 200 or body.get("code") != 1000:
        print("换发失败：请核对 client_id / client_secret 与应用启用状态")
        sys.exit(1)
    return body["data"]


def call_with_token(base_url, access_token, path="/api/system/userinfo"):
    """用凭证调用接口（Authorization: Pat <token>）。"""
    step(f"2. 调用接口（GET {path}）")
    resp = requests.get(
        f"{base_url}{path}",
        headers={"Authorization": f"Pat {access_token}"},
        timeout=10,
    )
    show(resp)
    if resp.status_code == 403:
        print("→ 403：该接口不在应用 scope / 资源授权范围内（只收敛不提权的预期行为）")
    if resp.status_code == 429:
        print("→ 429：触发应用的每分钟限流（rate_limit_per_minute）")
    return resp


def oauth_flow(base_url, client_id, client_secret, username, password, redirect_uri):
    """OAuth 授权码演示：登录 → 同意 → code 换 access → 代表用户调用。"""
    step("3. OAuth 授权码：登录 xadmin（代表用户访问的前置）")
    session = requests.Session()
    login = session.post(
        f"{base_url}/api/system/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    show(login)
    if login.status_code != 200 or login.json().get("code") != 1000:
        print("登录失败：跳过 OAuth 演示")
        return None

    step("3.1 同意页数据（GET /api/system/open/oauth/authorize）")
    authorize = session.get(
        f"{base_url}/api/system/open/oauth/authorize",
        params={"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": "demo"},
        timeout=10,
    )
    show(authorize)

    step("3.2 用户同意（POST /api/system/open/oauth/approve）")
    approve = session.post(
        f"{base_url}/api/system/open/oauth/approve",
        json={"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "approved": True},
        timeout=10,
    )
    body = show(approve)
    code = (body.get("data") or {}).get("code")
    if not code:
        print("未取得授权码，跳过 OAuth 演示")
        return None

    step("3.3 授权码换发（POST /api/system/open/oauth/token）")
    token_resp = requests.post(
        f"{base_url}/api/system/open/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    payload = show(token_resp)
    access = (payload.get("data") or {}).get("access_token")
    if access:
        step("3.4 代表用户调用（Authorization: Pat <oauth access>）")
        show(
            requests.get(
                f"{base_url}/api/system/userinfo",
                headers={"Authorization": f"Pat {access}"},
                timeout=10,
            )
        )
    return payload


class _WebhookReceiver(BaseHTTPRequestHandler):
    secret = ""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        timestamp = self.headers.get("X-Xadmin-Timestamp", "")
        signature = self.headers.get("X-Xadmin-Signature", "")
        expected = (
            "sha256="
            + hmac.new(self.secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        )
        ok = hmac.compare_digest(signature, expected)
        print(f"[webhook] event={self.headers.get('X-Xadmin-Event')} 验签={'通过' if ok else '失败'}")
        print(f"[webhook] payload={body.decode('utf-8')[:300]}")
        self.send_response(200 if ok else 400)
        self.end_headers()

    def log_message(self, *args):
        pass


def receive_webhook(port, secret, once=True):
    """本地接收端：验证签名头（sha256=HMAC(secret, "{timestamp}.{body}")）。"""
    step(f"webhook 接收端已启动：http://127.0.0.1:{port}/hook（Ctrl+C 退出）")
    _WebhookReceiver.secret = secret
    server = HTTPServer(("127.0.0.1", port), _WebhookReceiver)
    if once:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        thread.join(timeout=120)
    else:
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="xadmin 开放平台接入示例")
    parser.add_argument("--base-url", default="http://127.0.0.1:8896")
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument("--oauth", action="store_true", help="追加 OAuth 授权码演示")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--redirect-uri", default="https://example.com/cb")
    parser.add_argument("--receive-webhook", action="store_true", help="只启动 webhook 验签接收端")
    parser.add_argument("--webhook-port", type=int, default=18898)
    parser.add_argument("--webhook-secret", default="", help="订阅创建时填写的密钥")
    args = parser.parse_args()

    if args.receive_webhook:
        receive_webhook(args.webhook_port, args.webhook_secret)
        return

    if not args.client_id or not args.client_secret:
        parser.error("需要 --client-id 与 --client-secret（或使用 --receive-webhook）")

    print(f"目标服务：{args.base_url}")
    issued = issue_token(args.base_url, args.client_id, args.client_secret)
    call_with_token(args.base_url, issued["access_token"])
    if args.oauth:
        if not args.username or not args.password:
            parser.error("--oauth 需要 --username 与 --password")
        oauth_flow(args.base_url, args.client_id, args.client_secret, args.username, args.password, args.redirect_uri)
    print("\n完成：接入方已可换发凭证并按应用授权面调用接口。")


if __name__ == "__main__":
    main()
