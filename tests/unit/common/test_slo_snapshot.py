#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SLO 快照脚本单测：指标文本解析与四项 SLO 计算（缺数据路径）+ cron 接线端到端。"""

import datetime
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from scripts.slo_snapshot import (
    append_snapshot,
    build_snapshot_record,
    compute_slo,
    parse_label_pairs,
    parse_metrics,
)


class TestParse:
    def test_parse_label_pairs(self):
        assert parse_label_pairs('method="GET",status="200"') == {"method": "GET", "status": "200"}
        assert parse_label_pairs("") == {}

    def test_parse_metrics_skips_comments_and_invalid(self):
        text = "\n".join(
            [
                "# HELP xadmin_http_requests_total 说明",
                "# TYPE xadmin_http_requests_total counter",
                "",
                'xadmin_http_requests_total{method="GET",status="200"} 3.0',
                "not a sample line",
            ]
        )
        samples = parse_metrics(text)
        assert len(samples) == 1
        assert samples[0]["name"] == "xadmin_http_requests_total"
        assert samples[0]["labels"]["status"] == "200"
        assert samples[0]["value"] == 3.0


class TestComputeSlo:
    def test_availability_and_task_success(self):
        samples = [
            {"name": "xadmin_http_requests_total", "labels": {"status": "200"}, "value": 95.0},
            {"name": "xadmin_http_requests_total", "labels": {"status": "500"}, "value": 5.0},
            {"name": "xadmin_celery_tasks_total", "labels": {"status": "SUCCESS"}, "value": 9.0},
            {"name": "xadmin_celery_tasks_total", "labels": {"status": "FAILURE"}, "value": 1.0},
        ]
        result = compute_slo(samples)
        assert result["availability"]["value"] == 0.95
        assert result["availability"]["5xx"] == 5.0
        assert result["task_success"]["value"] == 0.9

    def test_p95_bucket_approximation(self):
        # 100 个样本：90 个 <=0.05s，10 个 <=0.5s → 95% 分位落在 0.5 桶
        samples = [
            {"name": "xadmin_http_request_duration_seconds_bucket", "labels": {"le": "0.05"}, "value": 90.0},
            {"name": "xadmin_http_request_duration_seconds_bucket", "labels": {"le": "0.5"}, "value": 100.0},
            {"name": "xadmin_http_request_duration_seconds_bucket", "labels": {"le": "+Inf"}, "value": 100.0},
        ]
        result = compute_slo(samples)
        assert result["p95_seconds"]["value"] == 0.5
        assert result["p95_seconds"]["samples"] == 100.0

    def test_missing_data_paths(self):
        result = compute_slo([])
        assert result["availability"]["value"] is None
        assert result["p95_seconds"]["value"] is None
        assert result["task_success"]["value"] is None
        assert result["queue_backlog"]["value"] is None
        assert "note" in result["availability"]


class TestSnapshotAccumulation:
    """长期累积（--append）：JSONL 记录形状与追加语义，供 SLO 校准采集。"""

    def test_record_shape_has_utc_ts_and_result(self):
        moment = datetime.datetime(2034, 10, 1, 6, 17, tzinfo=datetime.UTC)
        record = build_snapshot_record(compute_slo([]), now=moment)
        assert record["ts"] == "2034-10-01T06:17:00+00:00"
        assert set(record["result"]) >= {"availability", "p95_seconds", "task_success", "queue_backlog"}

    def test_append_snapshot_writes_jsonl(self, tmp_path):
        target = tmp_path / "nested" / "slo_snapshots.jsonl"
        append_snapshot(str(target), build_snapshot_record(compute_slo([])))
        append_snapshot(str(target), build_snapshot_record(compute_slo([])))
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            payload = json.loads(line)
            assert "ts" in payload and "result" in payload


class TestCronScriptWiring:
    """utils/slo_snapshot_cron.sh 接线端到端：stub 指标端点 → 脚本 → --append 落盘。

    采集机制（每日 cron）的正确性取决于三件事：环境变量透传（URL/令牌/累积文件）、
    Bearer 令牌到达端点、快照以 JSONL 追加。本测试用进程内 stub HTTP 服务验证全链路，
    不依赖真实容器。
    """

    SERVER_ROOT = Path(__file__).resolve().parents[3]

    @staticmethod
    def _serve_metrics(payload: str, seen_headers: list):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 与基类签名一致
                seen_headers.append(dict(self.headers))
                body = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 静默测试输出
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_cron_script_appends_snapshot_via_stub_endpoint(self, tmp_path):
        payload = "\n".join(
            [
                'xadmin_http_requests_total{method="GET",status="200"} 95.0',
                'xadmin_http_requests_total{method="GET",status="500"} 5.0',
                'xadmin_http_request_duration_seconds_bucket{le="0.05"} 90.0',
                'xadmin_http_request_duration_seconds_bucket{le="0.5"} 100.0',
                'xadmin_http_request_duration_seconds_bucket{le="+Inf"} 100.0',
                'xadmin_celery_tasks_total{status="SUCCESS"} 9.0',
                'xadmin_celery_tasks_total{status="FAILURE"} 1.0',
                "",
            ]
        )
        seen_headers: list = []
        server = self._serve_metrics(payload, seen_headers)
        try:
            target = tmp_path / "slo_snapshots.jsonl"
            env = {
                **os.environ,
                "METRICS_URL": f"http://127.0.0.1:{server.server_address[1]}/metrics",
                "METRICS_TOKEN": "slo-wiring-token",
                "SLO_SNAPSHOT_FILE": str(target),
                "PYTHON": sys.executable,
            }
            script = self.SERVER_ROOT / "utils" / "slo_snapshot_cron.sh"
            subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60, check=True)
        finally:
            server.shutdown()

        # 令牌以 Bearer 头到达端点（鉴权接线正确）
        assert any(h.get("Authorization") == "Bearer slo-wiring-token" for h in seen_headers)
        # 快照以 JSONL 追加且数值与 stub 数据一致
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "ts" in record and "result" in record
        assert record["result"]["availability"]["value"] == 0.95
        assert record["result"]["p95_seconds"]["value"] == 0.5
        assert record["result"]["task_success"]["value"] == 0.9

    def test_cron_script_syntax_and_contract(self):
        """脚本语法有效，且默认端点/累积文件/--append 接线保留（文本级回归）。"""
        script = self.SERVER_ROOT / "utils" / "slo_snapshot_cron.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text(encoding="utf-8")
        assert "--append" in text
        assert "SLO_SNAPSHOT_FILE" in text
        assert "METRICS_TOKEN" in text
        assert "slo_snapshot.py" in text
