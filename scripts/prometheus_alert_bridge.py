#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus 告警桥接：把 firing 告警投递进平台运维告警通道（站内信 + 邮件 + Webhook）。

动机：告警规则（``utils/monitoring/alerts.yml``）只做判定；单机部署不额外引入
Alertmanager，用本脚本轮询 Prometheus 的 ``/api/v1/alerts``，把 firing 告警写进
平台既有的 ``POST /api/common/api/ops-alert``（令牌 ``OPS_ALERT_TOKEN``）——
与容器 OOM 告警同一条投递链路（超管站内信 + 邮件 + 出站 Webhook ``system.ops_alert``）。

行为：
- 轮询取 ``state == "firing"`` 的告警；
- 去重：按 ``fingerprint`` 记录「最近一次投递时间」到状态文件，重复抑制窗口
  ``PROMETHEUS_ALERT_REPEAT_SECONDS``（默认 4h）内不重发；告警从响应中消失（resolved）
  即清除记录，**再次 firing 立即通知**（不等窗口）；
- 投递失败不推进该告警的时间戳（下一轮重试），其它告警不受影响；
- 单次运行模式：适合 systemd timer / cron（每分钟一次），出错只记 stderr 并返回非零。

用法::

    PROMETHEUS_URL=http://127.0.0.1:9090 \
    OPS_ALERT_URL=https://xadmin.example.com/api/common/api/ops-alert \
    OPS_ALERT_TOKEN=xxx \
    python3 scripts/prometheus_alert_bridge.py [--state /var/lib/xadmin/prometheus_alerts.json] [--dry-run]

退出码：0 正常（含"无 firing 告警"）；1 拉取/投递失败；2 参数缺失。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_STATE_FILE = "/tmp/xadmin-prometheus-alerts.json"
DEFAULT_REPEAT_SECONDS = 4 * 3600
DETAIL_MAX = 2000


def fetch_json(url: str, timeout: int = 10) -> dict:
    """GET JSON（Prometheus HTTP API）。"""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 固定内网端点
        return json.loads(response.read().decode())


def select_firing(payload: dict) -> list:
    """从 ``/api/v1/alerts`` 响应中取正在 firing 的告警（``data.alerts``）。"""
    alerts = ((payload or {}).get("data") or {}).get("alerts") or []
    return [alert for alert in alerts if str((alert or {}).get("state")) == "firing"]


def alert_fingerprint(alert: dict) -> str:
    """告警指纹：优先用 Prometheus 提供的 fingerprint，缺失时按关键 label 组拼。"""
    labels = alert.get("labels") or {}
    provided = str(alert.get("fingerprint") or "").strip()
    if provided:
        return provided
    key = "|".join(str(labels.get(name) or "") for name in ("alertname", "instance", "severity", "queue", "job"))
    return key or "unknown"


def build_alert_payload(alert: dict, now: datetime.datetime | None = None) -> dict:
    """映射为平台运维告警载荷（source/event/host/time/detail；不含内部字段）。"""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    name = str(labels.get("alertname") or "unknown")
    severity = str(labels.get("severity") or "")
    instance = str(labels.get("instance") or labels.get("queue") or labels.get("job") or "prometheus")
    lines = []
    summary = str(annotations.get("summary") or "").strip()
    description = str(annotations.get("description") or "").strip()
    if summary:
        lines.append(summary)
    if description:
        lines.append(description)
    label_text = ", ".join(f"{key}={value}" for key, value in sorted(labels.items()))
    if label_text:
        lines.append(f"labels: {label_text}")
    started = str(alert.get("activeAt") or "").strip()
    moment = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "source": "prometheus",
        "event": f"{name} [{severity}]" if severity else name,
        "host": instance,
        "time": started or moment,
        "detail": "\n".join(lines)[:DETAIL_MAX],
    }


def post_ops_alert(url: str, token: str, payload: dict, timeout: int = 10) -> bool:
    """投递到 ``/api/common/api/ops-alert``；服务端 60s 节流会把重复投递合并为 False。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "xadmin-prometheus-bridge/1.0",
            "X-Ops-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 固定内网端点
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError):
        return False


def load_state(path: str) -> dict:
    """状态文件：``{"fingerprints": {"<fp>": "<iso 时间>"}}``；损坏/缺失按空处理。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fingerprints": {}}
    fingerprints = data.get("fingerprints")
    return {"fingerprints": fingerprints if isinstance(fingerprints, dict) else {}}


def save_state(path: str, state: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def due_alerts(alerts: list, state: dict, now: float, repeat_seconds: int) -> list:
    """过滤出「应当投递」的告警：新出现，或距上次投递超过重复抑制窗口。"""
    notified = state.get("fingerprints") or {}
    due = []
    for alert in alerts:
        fingerprint = alert_fingerprint(alert)
        last = notified.get(fingerprint)
        if last is None:
            due.append(alert)
            continue
        try:
            last_ts = datetime.datetime.fromisoformat(str(last)).timestamp()
        except (TypeError, ValueError):
            due.append(alert)
            continue
        if now - last_ts >= repeat_seconds:
            due.append(alert)
    return due


def run_once(
    prometheus_url: str,
    ops_url: str,
    token: str,
    state_path: str,
    repeat_seconds: int = DEFAULT_REPEAT_SECONDS,
    dry_run: bool = False,
    now: datetime.datetime | None = None,
) -> dict:
    """单轮：拉取 → 去重 → 投递 → 落状态；返回摘要（供日志与测试断言）。"""
    moment = now or datetime.datetime.now()
    payload = fetch_json(f"{prometheus_url.rstrip('/')}/api/v1/alerts")
    firing = select_firing(payload)
    state = load_state(state_path)
    due = due_alerts(firing, state, moment.timestamp(), repeat_seconds)

    live = {alert_fingerprint(alert) for alert in firing}
    # 已恢复（不在本次 firing 集合内）的告警清除记录：再次 firing 时立即通知
    state["fingerprints"] = {key: value for key, value in (state.get("fingerprints") or {}).items() if key in live}

    delivered, failed = [], []
    for alert in due:
        fingerprint = alert_fingerprint(alert)
        if dry_run:
            delivered.append(fingerprint)
            continue
        if post_ops_alert(ops_url, token, build_alert_payload(alert, now=moment)):
            state["fingerprints"][fingerprint] = moment.isoformat(timespec="seconds")
            delivered.append(fingerprint)
        else:
            failed.append(fingerprint)

    if not dry_run:
        save_state(state_path, state)
    return {"firing": len(firing), "due": len(due), "delivered": delivered, "failed": failed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prometheus 告警桥接（firing -> ops-alert）")
    parser.add_argument(
        "--prometheus-url", default=os.environ.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL), help="Prometheus 地址"
    )
    parser.add_argument("--ops-url", default=os.environ.get("OPS_ALERT_URL", ""), help="ops-alert 端点 URL")
    parser.add_argument("--token", default=os.environ.get("OPS_ALERT_TOKEN", ""), help="OPS_ALERT_TOKEN")
    parser.add_argument(
        "--state", default=os.environ.get("PROMETHEUS_ALERT_STATE", DEFAULT_STATE_FILE), help="重复抑制状态文件"
    )
    parser.add_argument(
        "--repeat-seconds",
        type=int,
        default=int(os.environ.get("PROMETHEUS_ALERT_REPEAT_SECONDS", DEFAULT_REPEAT_SECONDS)),
        help="同一告警最小重发间隔（秒，默认 4h）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印应当投递的告警，不投递、不落状态")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.ops_url or not args.token:
        print("缺少 OPS_ALERT_URL / OPS_ALERT_TOKEN（环境变量或参数）", file=sys.stderr)
        return 2
    try:
        result = run_once(
            args.prometheus_url,
            args.ops_url,
            args.token,
            args.state,
            args.repeat_seconds,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 脚本入口统一报错（下一轮重试）
        print(f"拉取 Prometheus 告警失败：{exc}", file=sys.stderr)
        return 1
    print(
        f"firing={result['firing']} due={result['due']} "
        f"delivered={len(result['delivered'])} failed={len(result['failed'])}"
    )
    for fingerprint in result["delivered"]:
        print(f"  delivered: {fingerprint}")
    for fingerprint in result["failed"]:
        print(f"  FAILED   : {fingerprint}", file=sys.stderr)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
