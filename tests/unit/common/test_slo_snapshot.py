#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SLO 快照脚本单测：指标文本解析与四项 SLO 计算（缺数据路径）。"""

from scripts.slo_snapshot import compute_slo, parse_label_pairs, parse_metrics


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
