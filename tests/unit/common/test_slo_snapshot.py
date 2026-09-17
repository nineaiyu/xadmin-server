#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SLO 快照脚本单测：指标文本解析与四项 SLO 计算（缺数据路径）。"""

import datetime
import json

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
