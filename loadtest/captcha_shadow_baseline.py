#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""影子期合成流量基线（本地/预发执行）。

向 challenge 发送不同环境画像的挑战请求，统计响应分布，供切 enforce 前做
「若开启会放行/拦截多少」的粗基线。真正的风险决策在 CaptchaLog 的
`would_decision` 字段，管理员可在「验证数据」页签查看。

用法::

    python loadtest/captcha_shadow_baseline.py --base-url http://127.0.0.1:8896 --rounds 50

注意：本脚本只造 challenge 流量，不 verify；不会产生通过率，也不会修改配置。
"""

import argparse
import random
import statistics
import time

import requests

PROFILES = (
    {"name": "clean", "env": {"v": 1, "timezone": "Asia/Shanghai", "language": "zh-CN"}},
    {"name": "headless", "env": {"v": 1, "webdriver": True, "timezone": "Asia/Shanghai", "language": "zh-CN"}},
    {"name": "bad-tz", "env": {"v": 1, "timezone": "Not/AZone", "language": "zh-CN"}},
    {"name": "missing-env", "env": None},
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8896")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--interval", type=float, default=2.1, help="请求间隔秒；默认 2.1 以避开 30/min 限流")
    parser.add_argument("--scene", default="login")
    parser.add_argument("--action", default="identity.login.basic")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    results = []
    for index in range(args.rounds):
        profile = PROFILES[index % len(PROFILES)]
        payload = {"scene": args.scene, "action": args.action}
        if profile["env"] is not None:
            payload["env"] = profile["env"]
        started = time.perf_counter()
        try:
            response = requests.post(f"{base}/api/identity/auth/captcha/challenge", json=payload, timeout=15)
            latency = (time.perf_counter() - started) * 1000
            results.append({"profile": profile["name"], "status": response.status_code, "ms": latency})
        except requests.RequestException:
            results.append({"profile": profile["name"], "status": 0, "ms": 0})
        time.sleep(max(args.interval + random.uniform(-0.2, 0.2), 0.1))
    by_profile: dict[str, list[int]] = {}
    for row in results:
        by_profile.setdefault(row["profile"], []).append(row["status"])
    summary = {
        name: {
            "count": len(statuses),
            "ok": sum(1 for status in statuses if status == 200),
            "throttled": sum(1 for status in statuses if status == 429),
            "errors": sum(1 for status in statuses if status not in (200, 429)),
        }
        for name, statuses in by_profile.items()
    }
    latencies = [row["ms"] for row in results if row["status"] == 200]
    summary["latency_p50_ms"] = round(statistics.median(latencies), 1) if latencies else 0
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
