#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证码红队烟雾脚本（发布前手工执行，不进 CI）。

验证最低成本攻击面：
- 旧图片码路由必须 404；
- 无 env / 无签名 / 无 nonce 的 verify 必须被拒；
- 不存在的 challenge 必须 400；
- 登录携带旧 captcha_key/captcha_code 必须失败。

用法::

    python loadtest/captcha_attack.py --base-url http://127.0.0.1:8896

说明：这不是完整的 CV/OCR 红队；滑块模板匹配、图片 OCR、点选模型需另用带
OpenCV/Tesseract 的环境跑，本脚本只守住协议层与 clean cut。
"""

import argparse
import json
import sys

import requests

OLD_ROUTES = (
    "/api/identity/auth/captcha",
    "/api/identity/auth/captcha/inspect",
    "/api/system/captcha/refresh/",
)


def _json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8896")
    parser.add_argument("--scene", default="login")
    parser.add_argument("--action", default="identity.login.basic")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    failures: list[str] = []

    for path in OLD_ROUTES:
        response = requests.get(f"{base}{path}", timeout=10)
        if response.status_code != 404:
            failures.append(f"旧路由未 404：{path} -> {response.status_code}")

    challenge = requests.post(
        f"{base}/api/identity/auth/captcha/challenge",
        json={"scene": args.scene, "action": args.action},
        timeout=10,
    )
    if challenge.status_code != 200:
        failures.append(f"challenge 不可用：{challenge.status_code} {challenge.text[:200]}")
    data = _json(challenge).get("data") or {}
    challenge_id = data.get("challenge_id", "")

    verify = requests.post(
        f"{base}/api/identity/auth/captcha/verify",
        json={
            "challenge_id": challenge_id,
            "type": data.get("type", "slide"),
            "scene": args.scene,
            "answer": {"x": -99999},
            "action": args.action,
        },
        timeout=10,
    )
    if verify.status_code != 400:
        failures.append(f"无签名 verify 未被拒：{verify.status_code} {verify.text[:200]}")

    missing = requests.post(
        f"{base}/api/identity/auth/captcha/verify",
        json={
            "challenge_id": "does-not-exist",
            "type": "slide",
            "scene": args.scene,
            "answer": {"x": 1},
            "action": args.action,
        },
        timeout=10,
    )
    if missing.status_code != 400:
        failures.append(f"未知 challenge 未被拒：{missing.status_code}")

    legacy_login = requests.post(
        f"{base}/api/identity/login/basic",
        json={"username": "admin", "password": "wrong", "captcha_key": "legacy", "captcha_code": "1234"},
        timeout=10,
    )
    if legacy_login.status_code not in (400, 401):
        failures.append(f"旧 captcha_key/code 登录未被拒：{legacy_login.status_code}")

    if failures:
        print("[captcha-attack] 失败：")
        for item in failures:
            print("  -", item)
        return 1
    print(json.dumps({"passed": True, "challenge_type": data.get("type"), "checks": 6}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
