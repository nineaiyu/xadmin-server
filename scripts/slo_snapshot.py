#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SLO 快照：从指标端点拉取数据并计算 SLO 当前观测值（校准与日常观测两用）。

SLO 四项（定义见 docs/ops/observability.md §三）：

1. **HTTP 可用性** = 1 - 5xx 率（``xadmin_http_requests_total`` 按 ``status`` 聚合）；
2. **API P95 延迟** = ``xadmin_http_request_duration_seconds`` 直方图（跨 view 汇总，
   桶级近似：返回累计 ≥95% 的首桶 ``le``）；
3. **任务成功率** = ``xadmin_celery_tasks_total`` 的 SUCCESS / total（跨进程聚合口径）；
4. **队列积压** = 不在指标端点内（broker 深度，用健康检查 / redis 口径核对）——标注跳过。

口径提醒：HTTP/任务指标为**进程启动起累计**（非月度），正式校准需结合长期数据
（见 observability §三「SLO 数据源与校准」）。

用法::

    METRICS_TOKEN=xxx python scripts/slo_snapshot.py [--url ...] [--json]

退出码：0 正常；1 拉取/解析/缺 token 失败。
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.request

# Prometheus 文本样本行：name{k="v",...} value
_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([0-9eE.+-]+)$")


def parse_label_pairs(raw: str) -> dict:
    """解析 label 串 ``a="b",c="d"`` -> dict。"""
    pairs = {}
    for item in (raw or "").split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        pairs[key.strip()] = value.strip().strip('"')
    return pairs


def parse_metrics(text: str) -> list:
    """Prometheus 文本 -> 样本列表（跳过 HELP/TYPE/注释与非法行）。"""
    samples = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        matched = _LINE_RE.match(line)
        if not matched:
            continue
        samples.append(
            {
                "name": matched.group(1),
                "labels": parse_label_pairs(matched.group(2)),
                "value": float(matched.group(3)),
            }
        )
    return samples


def compute_slo(samples: list) -> dict:
    """由样本计算 SLO 观测值；缺数据项 value=None 并附说明。"""
    result = {}

    # 1) HTTP 可用性
    total = 0.0
    server_errors = 0.0
    for sample in samples:
        if sample["name"] != "xadmin_http_requests_total":
            continue
        total += sample["value"]
        if str(sample["labels"].get("status", "")).startswith("5"):
            server_errors += sample["value"]
    if total:
        result["availability"] = {"total": total, "5xx": server_errors, "value": 1 - server_errors / total}
    else:
        result["availability"] = {"total": 0, "5xx": 0, "value": None, "note": "无 HTTP 请求样本"}

    # 2) P95 延迟（桶级近似：跨 view 汇总后找累计 >= 95% 的首桶）
    buckets: dict = {}
    for sample in samples:
        if sample["name"] != "xadmin_http_request_duration_seconds_bucket":
            continue
        le = sample["labels"].get("le", "")
        buckets[le] = buckets.get(le, 0.0) + sample["value"]
    if buckets:
        total_cnt = max(buckets.get("+Inf", 0.0), 1.0)
        target = math.ceil(total_cnt * 0.95)
        p95 = None
        for le_text in sorted((b for b in buckets if b != "+Inf"), key=float):
            if buckets[le_text] >= target:
                p95 = float(le_text)
                break
        result["p95_seconds"] = {
            "samples": total_cnt,
            "value": p95 if p95 is not None else float("inf"),
            "note": "桶级近似（返回累计 ≥95% 的首桶 le）",
        }
    else:
        result["p95_seconds"] = {"samples": 0, "value": None, "note": "无延迟样本"}

    # 3) 任务成功率（跨进程聚合）
    task_total = 0.0
    task_success = 0.0
    for sample in samples:
        if sample["name"] != "xadmin_celery_tasks_total":
            continue
        task_total += sample["value"]
        if sample["labels"].get("status") == "SUCCESS":
            task_success += sample["value"]
    if task_total:
        result["task_success"] = {"total": task_total, "success": task_success, "value": task_success / task_total}
    else:
        result["task_success"] = {
            "total": 0,
            "success": 0,
            "value": None,
            "note": "无任务样本（跨进程聚合自 worker 首轮执行起有值）",
        }

    # 4) 队列积压（端点外）
    result["queue_backlog"] = {"value": None, "note": "指标端点不含 broker 深度（健康检查 / redis 口径核对）"}
    return result


def fetch(url: str, token: str, timeout: int = 10) -> str:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 固定内网端点
        return response.read().decode()


def render(result: dict) -> str:
    lines = [
        "SLO 快照（HTTP/任务为进程启动起累计，非月度口径——正式校准结合长期数据）",
        "=" * 70,
    ]
    availability = result["availability"]
    if availability["value"] is not None:
        lines.append(
            f"HTTP 可用性 : {availability['value'] * 100:.3f}%  "
            f"(total={availability['total']:.0f}, 5xx={availability['5xx']:.0f})"
        )
    else:
        lines.append(f"HTTP 可用性 : 无数据（{availability.get('note')}）")
    p95 = result["p95_seconds"]
    if p95["value"] is not None:
        lines.append(f"API P95 延迟: {p95['value']}s  (桶级近似, samples={p95['samples']:.0f})")
    else:
        lines.append(f"API P95 延迟: 无数据（{p95.get('note')}）")
    task = result["task_success"]
    if task["value"] is not None:
        lines.append(
            f"任务成功率  : {task['value'] * 100:.2f}%  (total={task['total']:.0f}, success={task['success']:.0f})"
        )
    else:
        lines.append(f"任务成功率  : 无数据（{task.get('note')}）")
    lines.append(f"队列积压    : 见健康检查 / redis 口径（{result['queue_backlog']['note']}）")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="SLO 快照（拉指标端点计算当前观测值）")
    parser.add_argument(
        "--url",
        default=os.environ.get("METRICS_URL", "http://localhost:8896/api/common/api/metrics"),
        help="指标端点 URL",
    )
    parser.add_argument("--token", default=os.environ.get("METRICS_TOKEN", ""), help="METRICS_TOKEN")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于归档比对）")
    args = parser.parse_args()

    if not args.token:
        print("缺少 METRICS_TOKEN（--token 或环境变量）", file=sys.stderr)
        return 1
    try:
        text = fetch(args.url, args.token)
    except Exception as exc:  # noqa: BLE001 脚本入口统一报错
        print(f"拉取指标失败：{exc}", file=sys.stderr)
        return 1
    result = compute_slo(parse_metrics(text))
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
