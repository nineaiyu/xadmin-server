#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三轮中位数聚合（loadtest/median_results.py）：有效轮次判定、中位数口径、
输出可被 check_baseline 直接消费（基线刷新链路端到端）。
"""

import json

import pytest

from loadtest.check_baseline import update_baseline
from loadtest.median_results import aggregate, main, round_is_clean, write_results


def _summary(p95: float, rps: float, failed: float = 0.0, trends: dict | None = None) -> dict:
    payload = {
        "rps": rps,
        "http_req_duration": {"_all": {"p95": p95}},
        "http_req_failed_rate": {"_all": failed},
    }
    if trends:
        payload["trends"] = {name: {"p95": value} for name, value in trends.items()}
    return payload


def _write_round(base, name: str, case: str, summary: dict) -> None:
    directory = base / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{case}.json").write_text(json.dumps(summary), encoding="utf-8")


class TestRoundValidity:
    def test_clean_round_requires_zero_failure_rate(self):
        assert round_is_clean(_summary(120.0, 30.0)) is True
        assert round_is_clean(_summary(120.0, 30.0, failed=0.02)) is False

    def test_round_without_failure_metric_is_invalid(self):
        assert round_is_clean({"rps": 1.0}) is False


class TestAggregate:
    def test_odd_rounds_take_middle_value(self, tmp_path):
        for index, (p95, rps) in enumerate([(100.0, 30.0), (200.0, 40.0), (120.0, 35.0)], start=1):
            _write_round(tmp_path, f"round{index}", "01-login", _summary(p95, rps))
        results, stats = aggregate([tmp_path / f"round{i}" for i in (1, 2, 3)], cases=("01-login",))
        assert results["01-login"]["rps"] == 35.0
        assert results["01-login"]["http_req_duration"]["_all"]["p95"] == 120.0
        assert stats["01-login"]["used"] == ["round1", "round2", "round3"]

    def test_even_rounds_average_middle_two(self, tmp_path):
        for index, (p95, rps) in enumerate([(100.0, 30.0), (200.0, 40.0)], start=1):
            _write_round(tmp_path, f"round{index}", "01-login", _summary(p95, rps))
        results, _ = aggregate([tmp_path / f"round{i}" for i in (1, 2)], cases=("01-login",))
        assert results["01-login"]["rps"] == 35.0
        assert results["01-login"]["http_req_duration"]["_all"]["p95"] == 150.0

    def test_dirty_round_excluded(self, tmp_path):
        _write_round(tmp_path, "round1", "01-login", _summary(100.0, 30.0))
        _write_round(tmp_path, "round2", "01-login", _summary(999.0, 1.0, failed=0.2033))
        _write_round(tmp_path, "round3", "01-login", _summary(200.0, 20.0))
        results, stats = aggregate([tmp_path / f"round{i}" for i in (1, 2, 3)], cases=("01-login",))
        assert results["01-login"]["http_req_duration"]["_all"]["p95"] == 150.0
        assert results["01-login"]["rounds"] == 2
        assert any("失败率非 0" in item for item in stats["01-login"]["skipped"])

    def test_trends_are_aggregated_per_bucket(self, tmp_path):
        for index, values in enumerate(
            [{"meta_columns": 80.0}, {"meta_columns": 120.0}, {"meta_columns": 100.0}], start=1
        ):
            _write_round(tmp_path, f"round{index}", "04-metadata", _summary(150.0, 100.0, trends=values))
        results, _ = aggregate([tmp_path / f"round{i}" for i in (1, 2, 3)], cases=("04-metadata",))
        assert results["04-metadata"]["trends"]["meta_columns"]["p95"] == 100.0

    def test_missing_case_is_reported_not_written(self, tmp_path):
        _write_round(tmp_path, "round1", "01-login", _summary(100.0, 30.0))
        _write_round(tmp_path, "round2", "01-login", _summary(200.0, 40.0))
        results, stats = aggregate([tmp_path / "round1", tmp_path / "round2"], cases=("01-login", "06-import"))
        assert "06-import" not in results
        assert stats["06-import"]["used"] == []


class TestBaselineRefreshChain:
    """输出 → check_baseline --update：中位数结果必须能被基线刷新消费。"""

    def test_median_output_updates_baseline(self, tmp_path):
        for index, (p95, rps) in enumerate([(30.0, 700.0), (34.0, 680.0), (32.0, 690.0)], start=1):
            _write_round(tmp_path, f"round{index}", "02-routes", _summary(p95, rps, trends={"meta": 10.0}))
        results, _ = aggregate([tmp_path / f"round{i}" for i in (1, 2, 3)], cases=("02-routes",))
        out_dir = tmp_path / "median"
        write_results(results, out_dir)

        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tolerance": {"p95_ratio": 1.2, "rps_ratio": 0.8, "max_error_rate": 0.01},
                    "cases": {"02-routes": {"p95": 61.8, "rps": 682.0}},
                }
            ),
            encoding="utf-8",
        )
        update_baseline(baseline_path, out_dir, note="median refresh")
        saved = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert saved["cases"]["02-routes"]["p95"] == 32.0
        assert saved["cases"]["02-routes"]["rps"] == 690.0
        assert saved["cases"]["02-routes"]["trends"]["meta"] == 10.0
        assert saved["env"]["note"] == "median refresh"


class TestCli:
    def test_missing_round_dir_exits_2(self, tmp_path, capsys):
        assert main([str(tmp_path / "nope"), str(tmp_path / "nope2")]) == 2
        assert "轮次目录不存在" in capsys.readouterr().err

    def test_single_round_rejected(self, tmp_path, capsys):
        (tmp_path / "round1").mkdir()
        assert main([str(tmp_path / "round1")]) == 2
        assert "至少需要两个轮次目录" in capsys.readouterr().err

    def test_all_invalid_exits_2(self, tmp_path, capsys):
        for index in (1, 2):
            _write_round(tmp_path, f"round{index}", "01-login", _summary(100.0, 30.0, failed=0.5))
        code = main([str(tmp_path / "round1"), str(tmp_path / "round2"), "--out", str(tmp_path / "median")])
        assert code == 2
        assert "全部用例都无有效轮次" in capsys.readouterr().err

    def test_success_writes_median_dir(self, tmp_path, capsys):
        for index, p95 in enumerate([100.0, 200.0], start=1):
            _write_round(tmp_path, f"round{index}", "01-login", _summary(p95, 30.0))
        out = tmp_path / "median"
        code = main([str(tmp_path / "round1"), str(tmp_path / "round2"), "--out", str(out), "--cases", "01-login"])
        assert code == 0
        assert (out / "01-login.json").is_file()
        assert "中位数结果已写入" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["01-login", "02-routes"])
def test_default_cases_cover_k6_scripts(case):
    """默认用例清单必须与实际 k6 脚本同名（脚本改名/新增时同步）。"""
    from loadtest.median_results import DEFAULT_CASES

    assert case in DEFAULT_CASES
