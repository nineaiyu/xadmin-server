#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证码挑战端点轻量压测（发布前手工执行，不进 CI）。

用法::

    python loadtest/captcha_load.py --base-url http://127.0.0.1:8896 --total 100 --concurrency 10

只压 challenge（不 verify），用于确认出图/缓存路径在并发下无 5xx、P95 可接受；
真实容量基线应在独立测试环境用 k6 按 100/500/1000 RPS 跑。
"""

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import requests

SCENES = ("login",)


def _one(base: str, index: int) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        response = requests.post(
            f"{base}/api/identity/auth/captcha/challenge",
            json={
                "scene": SCENES[index % len(SCENES)],
                "action": "identity.login.basic",
            },
            timeout=15,
        )
        return response.status_code, (time.perf_counter() - started) * 1000
    except requests.RequestException:
        return 0, (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8896")
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda index: _one(base, index), range(args.total)))
    elapsed = time.perf_counter() - started
    statuses = [status for status, _ in results]
    latencies = sorted(latency for _, latency in results)
    errors = sum(1 for status in statuses if status not in (200, 429))
    throttled = sum(1 for status in statuses if status == 429)
    p50 = statistics.median(latencies) if latencies else 0
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0
    summary = {
        "total": args.total,
        "concurrency": args.concurrency,
        "errors": errors,
        "throttled_429": throttled,
        "rps": round(args.total / elapsed, 1) if elapsed else 0,
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "max_ms": round(latencies[-1], 1) if latencies else 0,
    }
    print(summary)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
