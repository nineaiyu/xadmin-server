# -*- coding: utf-8 -*-
"""loadtest.check_baseline 压测基线回归比对测试。

覆盖：三项判定（P95 / RPS / 错误率）的通过与劣化分支、Trend 分档（04 元数据
三变体）、旧结果缺 rps 字段的回退计算、缺失结果文件、以及 --update 刷新快照。
"""

import json

import pytest

from loadtest.check_baseline import (
    compare,
    main,
    metric_error_rate,
    metric_p95,
    metric_rps,
    metric_trends,
    render_markdown,
    render_table,
)

BASELINE = {
    "tolerance": {"p95_ratio": 1.2, "rps_ratio": 0.8, "max_error_rate": 0.01},
    "cases": {
        "01-login": {"label": "登录", "rps": 100.0, "p95": 100.0},
        "04-metadata": {
            "label": "元数据三变体",
            "rps": 100.0,
            "p95": 100.0,
            "trends": {"meta_columns_duration": 80.0, "meta_fields_duration": 80.0},
        },
    },
}


def summary(p95=100.0, rps=100.0, failed=0.0, trends=None, with_rps=True):
    """构造 k6 结果 JSON（字段与 lib.js makeSummary 输出一致）。"""
    data = {
        "recorded_at": "2026-09-08T00:00:00.000Z",
        "base_url": "http://127.0.0.1:8896",
        "iterations": 1000,
        "http_reqs": 6000,
        "duration_ms": 60000,
        "http_req_duration": {"_all": {"avg": p95, "p50": p95, "p90": p95, "p95": p95, "p99": p95, "max": p95}},
        "http_req_failed_rate": {"_all": failed},
    }
    if with_rps:
        data["rps"] = rps
    if trends:
        data["trends"] = {name: {"p95": value} for name, value in trends.items()}
    return data


def write_results(tmp_path, files):
    results = tmp_path / "results"
    results.mkdir()
    for name, data in files.items():
        (results / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return results


def write_baseline(tmp_path, data=None):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(data or BASELINE), encoding="utf-8")
    return path


def run_main(tmp_path, files, baseline=None, extra=None):
    baseline_path = write_baseline(tmp_path, baseline)
    results = write_results(tmp_path, files)
    argv = ["--baseline", str(baseline_path), "--results", str(results)]
    if extra:
        argv += extra
    return main(argv), baseline_path, results


def test_metrics_extract_fields():
    data = summary(p95=173.9, rps=31.3, trends={"meta_columns_duration": 81.5})
    assert metric_p95(data) == 173.9
    assert metric_rps(data) == 31.3
    assert metric_error_rate(data) == 0.0
    assert metric_trends(data) == {"meta_columns_duration": 81.5}


def test_rps_fallback_without_rps_field():
    """旧结果没有 rps 字段时按 http_reqs / duration_ms 回退计算。"""
    data = summary(with_rps=False)
    assert metric_rps(data) == pytest.approx(100.0, rel=1e-3)


def test_p95_fallback_takes_max_group():
    """无 _all 档（k6 版本差异）时取各 group 最大值，避免漏报。"""
    data = {"http_req_duration": {"04a": {"p95": 80.0}, "04c": {"p95": 203.8}}}
    assert metric_p95(data) == 203.8


def test_all_pass(tmp_path):
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=110.0, rps=95.0),
            "04-metadata": summary(
                p95=110.0, rps=95.0, trends={"meta_columns_duration": 85.0, "meta_fields_duration": 85.0}
            ),
        },
    )
    assert code == 0


def test_p95_regression_fails(tmp_path):
    """P95 超出基线 1.2 倍即判劣化。"""
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=130.0, rps=100.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}
            ),
        },
    )
    assert code == 1


def test_rps_drop_fails(tmp_path):
    """吞吐跌破基线 0.8 倍即判劣化（即使延迟未变差）。"""
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=100.0, rps=70.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}
            ),
        },
    )
    assert code == 1


def test_error_rate_breach_fails(tmp_path):
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=100.0, rps=100.0, failed=0.05),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}
            ),
        },
    )
    assert code == 1


def test_trend_regression_fails_even_if_overall_p95_ok(tmp_path):
    """分档 Trend 劣化必须被抓到：04 三变体混跑时整体 _all 可能被其他变体拉平。"""
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=100.0, rps=100.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 200.0, "meta_fields_duration": 80.0}
            ),
        },
    )
    assert code == 1


def test_missing_trend_is_skipped_not_passed(tmp_path):
    """结果缺分档时记入 skipped，且不影响整体判定（整体 P95 仍参与）。"""
    write_baseline(tmp_path)
    results = write_results(
        tmp_path,
        {"01-login": summary(), "04-metadata": summary(p95=100.0, rps=100.0)},
    )
    report = compare(BASELINE, results, BASELINE["tolerance"], ("p95", "rps", "error"))
    assert any("meta_columns_duration" in item for item in report.skipped)
    assert report.ok


def test_missing_result_file_fails(tmp_path):
    code, _, _ = run_main(tmp_path, {"01-login": summary()})
    assert code == 1


def test_checks_option_disables_rps(tmp_path):
    """CI 宽松档跳过 RPS：吞吐受 runner 规格影响，与环境强相关。"""
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=100.0, rps=10.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}
            ),
        },
        extra=["--checks", "p95,error"],
    )
    assert code == 0


def test_tolerance_override(tmp_path):
    """--tolerance 覆盖快照内 P95 容差（CI 断崖档用）。"""
    code, _, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=250.0, rps=100.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}
            ),
        },
        extra=["--tolerance", "3.0", "--checks", "p95,error"],
    )
    assert code == 0


def test_update_rewrites_baseline(tmp_path):
    code, baseline_path, _ = run_main(
        tmp_path,
        {
            "01-login": summary(p95=120.0, rps=90.0),
            "04-metadata": summary(
                p95=100.0, rps=100.0, trends={"meta_columns_duration": 85.0, "meta_fields_duration": 85.0}
            ),
        },
        extra=["--update"],
    )
    assert code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["cases"]["01-login"]["p95"] == 120.0
    assert data["cases"]["01-login"]["rps"] == 90.0
    assert data["cases"]["04-metadata"]["trends"]["meta_columns_duration"] == 85.0


def test_missing_baseline_returns_input_error(tmp_path):
    results = write_results(tmp_path, {"01-login": summary()})
    assert main(["--baseline", str(tmp_path / "nope.json"), "--results", str(results)]) == 2


def test_missing_results_dir_returns_input_error(tmp_path):
    baseline_path = write_baseline(tmp_path)
    assert main(["--baseline", str(baseline_path), "--results", str(tmp_path / "nope")]) == 2


def test_render_outputs_contain_status(tmp_path):
    write_baseline(tmp_path)
    results = write_results(
        tmp_path,
        {
            "01-login": summary(p95=130.0),
            "04-metadata": summary(p95=100.0, trends={"meta_columns_duration": 80.0, "meta_fields_duration": 80.0}),
        },
    )
    report = compare(BASELINE, results, BASELINE["tolerance"], ("p95", "rps", "error"))
    assert "FAIL" in render_table(report)
    assert "❌" in render_markdown(report)


def test_real_baseline_snapshot_is_loadable():
    """仓库内基线快照须保持可解析且六用例齐备，防止手改坏 JSON。"""
    from loadtest.check_baseline import DEFAULT_BASELINE, load_json

    data = load_json(DEFAULT_BASELINE)
    assert set(data["cases"]) == {
        "01-login",
        "02-routes",
        "03-list",
        "04-metadata",
        "05-export",
        "06-import",
    }
