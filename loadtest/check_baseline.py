#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""压测基线回归比对（性能防退化门禁）。

把 k6 产出的结果 JSON（`loadtest/k6/results/<case>.json`）与基线快照
（`loadtest/baseline.json`）比对，按 P95 / RPS / 错误率三项判定是否劣化：

- P95：当前值 > 基线 × p95_ratio（默认 1.2）→ 劣化；
- RPS：当前值 < 基线 × rps_ratio（默认 0.8）→ 劣化；
- 错误率：当前值 > max_error_rate（默认 0.01）→ 劣化。

用法（详见 docs/ops/performance-baseline.md §八）：

```bash
# 常规回归：固定环境跑完 k6 后比对
.venv/bin/python loadtest/check_baseline.py

# 输出 markdown（贴 PR / GitHub step summary）
.venv/bin/python loadtest/check_baseline.py --format md

# CI 宽松档：只拦断崖式劣化，且跳过与环境强相关的 RPS 项
.venv/bin/python loadtest/check_baseline.py --checks p95,error --tolerance 3.0

# 固定环境重新测定后刷新快照（三轮中位数跑完后执行）
.venv/bin/python loadtest/check_baseline.py --update
```

退出码：0 全部通过 / 1 存在劣化 / 2 输入错误（缺基线或结果文件）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_BASELINE = HERE / "baseline.json"
DEFAULT_RESULTS = HERE / "k6" / "results"

CHECKS = ("p95", "rps", "error")


@dataclass
class Finding:
    """单条判定结果。"""

    case: str
    check: str
    baseline: float | None
    current: float | None
    limit: float | None
    ok: bool
    note: str = ""

    @property
    def ratio(self) -> float | None:
        """变化倍率（当前 / 基线），无可比基线时为 None。"""
        if self.baseline in (None, 0) or self.current is None:
            return None
        return self.current / self.baseline


@dataclass
class Report:
    """整体比对结果。"""

    findings: list[Finding] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    @property
    def ok(self) -> bool:
        return not self.failed and not self.missing


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def metric_p95(summary: dict[str, Any]) -> float | None:
    """整体 P95：优先 `_all`，无则取各 group 最大值（保守口径）。"""
    durations = summary.get("http_req_duration") or {}
    if not durations:
        return None
    if "_all" in durations:
        return durations["_all"].get("p95")
    values = [g.get("p95") for g in durations.values() if g.get("p95") is not None]
    return max(values) if values else None


def metric_rps(summary: dict[str, Any]) -> float | None:
    """吞吐：优先脚本直出的 rps，回退 http_reqs / 时长。"""
    rps = summary.get("rps")
    if isinstance(rps, (int, float)) and rps > 0:
        return float(rps)
    reqs = summary.get("http_reqs") or 0
    duration = summary.get("duration_ms") or 0
    if reqs and duration:
        return round(reqs / (duration / 1000), 2)
    return None


def metric_error_rate(summary: dict[str, Any]) -> float:
    rates = summary.get("http_req_failed_rate") or {}
    values = [v for v in rates.values() if isinstance(v, (int, float))]
    return max(values) if values else 0.0


def metric_trends(summary: dict[str, Any]) -> dict[str, float]:
    trends = summary.get("trends") or {}
    return {name: values.get("p95") for name, values in trends.items() if values.get("p95") is not None}


def evaluate(
    case: str,
    baseline: dict[str, Any],
    summary: dict[str, Any],
    tolerance: dict[str, float],
    checks: tuple[str, ...],
) -> tuple[list[Finding], list[str]]:
    """比对单个用例，返回（判定列表，跳过的分档名）。"""
    findings: list[Finding] = []
    skipped: list[str] = []

    p95 = metric_p95(summary)
    rps = metric_rps(summary)
    error_rate = metric_error_rate(summary)
    trends = metric_trends(summary)

    if "p95" in checks:
        base_p95 = baseline.get("p95")
        if base_p95 is None or p95 is None:
            skipped.append(f"{case}:p95（基线或结果缺 P95，跳过）")
        else:
            limit = round(base_p95 * tolerance["p95_ratio"], 2)
            findings.append(Finding(case, "p95", base_p95, p95, limit, p95 <= limit))

    if "rps" in checks:
        base_rps = baseline.get("rps")
        if base_rps is None or rps is None:
            skipped.append(f"{case}:rps（基线或结果缺 RPS，跳过）")
        else:
            limit = round(base_rps * tolerance["rps_ratio"], 2)
            findings.append(Finding(case, "rps", base_rps, rps, limit, rps >= limit))

    if "error" in checks:
        limit = tolerance["max_error_rate"]
        findings.append(Finding(case, "error", 0.0, error_rate, limit, error_rate <= limit))

    # 分档 Trend（如 04 元数据三变体）：基线登记了但结果缺失 → 跳过并提示，
    # 整体 P95 仍参与判定，避免老结果让分档保护静默失效
    for name, base_value in (baseline.get("trends") or {}).items():
        current = trends.get(name)
        if current is None:
            skipped.append(f"{case}:{name}（结果无该 Trend 分档，跳过）")
            continue
        if "p95" not in checks:
            continue
        limit = round(base_value * tolerance["p95_ratio"], 2)
        findings.append(Finding(f"{case}:{name}", "p95", base_value, current, limit, current <= limit))

    return findings, skipped


def compare(
    baseline_data: dict[str, Any],
    results_dir: Path,
    tolerance: dict[str, float],
    checks: tuple[str, ...],
) -> Report:
    report = Report()
    for case, baseline in (baseline_data.get("cases") or {}).items():
        path = results_dir / f"{case}.json"
        if not path.exists():
            report.missing.append(f"{case}（{path}）")
            continue
        findings, skipped = evaluate(case, baseline, load_json(path), tolerance, checks)
        report.findings.extend(findings)
        report.skipped.extend(skipped)
    return report


def render_table(report: Report) -> str:
    # 用例列宽自适应：分档名（如 04-metadata:meta_columns_duration）比用例名长
    width = max([len(f.case) for f in report.findings] + [12])
    lines = [
        f"{'用例':<{width}}{'项':<8}{'基线':>12}{'当前':>12}{'限值':>12}{'变化':>10}  判定",
        "-" * (width + 58),
    ]
    for f in report.findings:
        ratio = f"{f.ratio:.2f}x" if f.ratio is not None else "-"
        lines.append(
            f"{f.case:<{width}}{f.check:<8}{f.baseline or 0:>12.2f}{f.current or 0:>12.2f}"
            f"{f.limit or 0:>12.2f}{ratio:>10}  {'PASS' if f.ok else 'FAIL'}"
        )
    if report.skipped:
        lines.append("")
        lines.extend(f"跳过：{item}" for item in report.skipped)
    if report.missing:
        lines.append("")
        lines.extend(f"缺失结果：{item}" for item in report.missing)
    return "\n".join(lines)


def render_markdown(report: Report) -> str:
    lines = [
        "| 用例 | 项 | 基线 | 当前 | 限值 | 变化 | 判定 |",
        "|------|----|-----:|-----:|-----:|-----:|:---:|",
    ]
    for f in report.findings:
        ratio = f"{f.ratio:.2f}x" if f.ratio is not None else "-"
        lines.append(
            f"| {f.case} | {f.check} | {f.baseline or 0:.2f} | {f.current or 0:.2f} | "
            f"{f.limit or 0:.2f} | {ratio} | {'✅' if f.ok else '❌'} |"
        )
    if report.skipped:
        lines.append("")
        lines.append("**跳过项**：" + "；".join(report.skipped))
    if report.missing:
        lines.append("")
        lines.append("**缺失结果**：" + "；".join(report.missing))
    status = "✅ 无性能退化" if report.ok else "❌ 存在性能退化"
    lines.insert(0, f"### 压测基线回归：{status}\n")
    return "\n".join(lines)


def update_baseline(baseline_path: Path, results_dir: Path, note: str | None) -> dict[str, Any]:
    """用当前结果刷新基线快照（固定环境重新测定后执行）。"""
    data = load_json(baseline_path)
    updated: list[str] = []
    for case, baseline in (data.get("cases") or {}).items():
        path = results_dir / f"{case}.json"
        if not path.exists():
            continue
        summary = load_json(path)
        p95, rps = metric_p95(summary), metric_rps(summary)
        if p95 is not None:
            baseline["p95"] = round(p95, 2)
        if rps is not None:
            baseline["rps"] = rps
        trends = metric_trends(summary)
        for name, value in trends.items():
            baseline.setdefault("trends", {})[name] = round(value, 2)
        updated.append(case)
    data["updated_at"] = _today()
    if note:
        data.setdefault("env", {})["note"] = note
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    data["_updated"] = updated
    return data


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="压测基线回归比对")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="基线快照路径")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS, help="k6 结果目录")
    parser.add_argument("--tolerance", type=float, default=None, help="P95 容差倍数（覆盖快照内 tolerance）")
    parser.add_argument("--rps-ratio", type=float, default=None, help="RPS 下限比例（覆盖快照内 tolerance）")
    parser.add_argument("--max-error-rate", type=float, default=None, help="错误率上限（覆盖快照内 tolerance）")
    parser.add_argument("--checks", default=",".join(CHECKS), help=f"参与判定的项，逗号分隔：{','.join(CHECKS)}")
    parser.add_argument("--format", choices=["table", "md", "json"], default="table", help="输出格式")
    parser.add_argument("--update", action="store_true", help="用当前结果刷新基线快照")
    parser.add_argument("--update-note", default=None, help="刷新快照时写入 env.note 的说明")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.update:
        if not args.baseline.exists():
            print(f"基线快照不存在：{args.baseline}", file=sys.stderr)
            return 2
        data = update_baseline(args.baseline, args.results, args.update_note)
        print(f"基线快照已刷新：{args.baseline}（更新用例 {len(data.get('_updated', []))} 个）")
        return 0

    if not args.baseline.exists():
        print(f"基线快照不存在：{args.baseline}", file=sys.stderr)
        return 2
    if not args.results.exists():
        print(f"结果目录不存在：{args.results}", file=sys.stderr)
        return 2

    baseline_data = load_json(args.baseline)
    tolerance = dict(baseline_data.get("tolerance") or {})
    if args.tolerance is not None:
        tolerance["p95_ratio"] = args.tolerance
    if args.rps_ratio is not None:
        tolerance["rps_ratio"] = args.rps_ratio
    if args.max_error_rate is not None:
        tolerance["max_error_rate"] = args.max_error_rate
    tolerance.setdefault("p95_ratio", 1.2)
    tolerance.setdefault("rps_ratio", 0.8)
    tolerance.setdefault("max_error_rate", 0.01)

    checks = tuple(c.strip() for c in args.checks.split(",") if c.strip() in CHECKS)
    report = compare(baseline_data, args.results, tolerance, checks)

    if args.format == "md":
        print(render_markdown(report))
    elif args.format == "json":
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "findings": [f.__dict__ for f in report.findings],
                    "skipped": report.skipped,
                    "missing": report.missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_table(report))
        print()
        print("✅ 无性能退化" if report.ok else f"❌ {len(report.failed)} 项劣化")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
