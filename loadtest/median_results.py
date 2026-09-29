#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮中位数聚合：把 k6 各轮结果合成一套「中位数结果」，供基线快照刷新使用。

背景（docs/ops/performance-baseline.md §八「快照刷新纪律」）：基线数值口径是
**固定环境三轮取中位数**，但 k6 每轮会覆盖写 ``results/<case>.json``——手工保存三轮、
再逐指标取中位数既易错也不可复算。本脚本把该流程固定为：

```bash
cd xadmin-server/loadtest/k6
RESULT_DIR=results/round1 BASE_URL=... ./run-all.sh     # 第 1 轮
RESULT_DIR=results/round2 BASE_URL=... ./run-all.sh     # 第 2 轮
RESULT_DIR=results/round3 BASE_URL=... ./run-all.sh     # 第 3 轮

cd ../..
.venv/bin/python loadtest/median_results.py loadtest/k6/results/round1 loadtest/k6/results/round2 loadtest/k6/results/round3 \
    --out loadtest/k6/results/median
.venv/bin/python loadtest/check_baseline.py --update --results loadtest/k6/results/median \
    --update-note "2026-xx-xx 复测（三轮中位数），环境：xxx"
```

口径（与文档一致）：

- **异常轮次整轮作废**：某一轮该用例出现失败请求（``http_req_failed_rate`` 非 0，
  即 4xx/5xx 或超时）时，该轮不参与该用例的中位数计算；
- **中位数**：奇数轮取中间值、偶数轮取中间两值均值（``p95`` / ``rps`` / 各 ``trends.*.p95``）；
- **全轮无效/缺结果**的用例不写入输出，并在摘要里列出——避免 ``--update`` 把缺项当"不变"。

退出码：0 正常；2 输入错误（轮次目录不存在、全部用例都无有效数据）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = ("01-login", "02-routes", "03-list", "04-metadata", "05-export", "06-import")


def load_summary(round_dir: Path, case: str) -> dict | None:
    """读取某轮某用例的结果（缺失/损坏 → None，视为该轮该用例无效）。"""
    path = round_dir / f"{case}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def failure_rates(summary: dict) -> list:
    """该轮所有 group 的失败率（k6 的 http_req_failed rate）。"""
    rates = summary.get("http_req_failed_rate") or {}
    return [float(value) for value in rates.values() if isinstance(value, (int, float))]


def round_is_clean(summary: dict) -> bool:
    """零失败才算有效轮次（文档口径：有 throttled / 5xx 的整轮作废）。"""
    rates = failure_rates(summary)
    return bool(rates) and max(rates) == 0


def _median(values: list) -> float:
    return round(float(statistics.median(values)), 2)


def p95_of(summary: dict) -> float | None:
    """整体 P95：优先 `_all`，无则取各 group 最大值（与 check_baseline 同口径）。"""
    durations = summary.get("http_req_duration") or {}
    if not durations:
        return None
    if "_all" in durations:
        value = durations["_all"].get("p95")
        return float(value) if value is not None else None
    values = [float(g["p95"]) for g in durations.values() if g.get("p95") is not None]
    return max(values) if values else None


def aggregate_case(round_dirs: list, case: str) -> tuple[dict | None, dict]:
    """聚合单个用例：返回（中位数结果或 None，统计信息）。"""
    used, skipped = [], []
    p95s, rpss, trends = [], [], {}
    for round_dir in round_dirs:
        summary = load_summary(round_dir, case)
        if summary is None:
            skipped.append(f"{round_dir.name}:缺失")
            continue
        if not round_is_clean(summary):
            skipped.append(f"{round_dir.name}:失败率非 0（max={max(failure_rates(summary), default=0):.4f}）")
            continue
        p95 = p95_of(summary)
        rps = summary.get("rps")
        if p95 is None or not isinstance(rps, (int, float)):
            skipped.append(f"{round_dir.name}:缺 p95/rps")
            continue
        used.append(round_dir.name)
        p95s.append(float(p95))
        rpss.append(float(rps))
        for name, values in (summary.get("trends") or {}).items():
            if values.get("p95") is not None:
                trends.setdefault(name, []).append(float(values["p95"]))

    stats = {"used": used, "skipped": skipped}
    if not p95s:
        return None, stats
    result = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "median_of_rounds": used,
        "rounds": len(used),
        "rps": _median(rpss),
        "http_req_duration": {"_all": {"p95": _median(p95s)}},
        # 有效轮次失败率均为 0：中位数结果按 0 写入，供 check_baseline 的错误率项判定
        "http_req_failed_rate": {"_all": 0.0},
        "trends": {name: {"p95": _median(values)} for name, values in sorted(trends.items())},
    }
    if trends:
        result["trends_of_rounds"] = {name: len(values) for name, values in sorted(trends.items())}
    return result, stats


def aggregate(round_dirs: list, cases: tuple = DEFAULT_CASES) -> tuple[dict, dict]:
    """聚合全部用例：返回（{case: 中位数结果}，{case: 统计信息}）。"""
    results, stats = {}, {}
    for case in cases:
        result, case_stats = aggregate_case(round_dirs, case)
        stats[case] = case_stats
        if result is not None:
            results[case] = result
    return results, stats


def write_results(results: dict, out_dir: Path) -> list:
    """把中位数结果写进输出目录（喂给 ``check_baseline.py --results``）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for case, payload in results.items():
        path = out_dir / f"{case}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    return written


def render(stats: dict) -> str:
    lines = [f"{'用例':<14}{'有效轮次':<28}{'作废轮次'}", "-" * 88]
    for case, info in stats.items():
        used = ",".join(info["used"]) or "-"
        skipped = "; ".join(info["skipped"]) or "-"
        lines.append(f"{case:<14}{used:<28}{skipped}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="k6 多轮结果取中位数（基线刷新前置步骤）")
    parser.add_argument("rounds", nargs="+", type=Path, help="各轮结果目录（如 loadtest/k6/results/round1 ...）")
    parser.add_argument("--out", type=Path, default=HERE / "k6" / "results" / "median", help="中位数结果输出目录")
    parser.add_argument("--cases", default=",".join(DEFAULT_CASES), help="参与聚合的用例名（逗号分隔）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [str(round_dir) for round_dir in args.rounds if not round_dir.is_dir()]
    if missing:
        print(f"轮次目录不存在：{'、'.join(missing)}", file=sys.stderr)
        return 2
    if len(args.rounds) < 2:
        print("至少需要两个轮次目录才能取中位数", file=sys.stderr)
        return 2

    cases = tuple(name.strip() for name in args.cases.split(",") if name.strip())
    results, stats = aggregate(list(args.rounds), cases)
    if not results:
        print("全部用例都无有效轮次（检查失败率与结果目录）", file=sys.stderr)
        return 2

    written = write_results(results, args.out)
    print(render(stats))
    print()
    print(f"中位数结果已写入 {args.out}（{len(written)} 个用例）")
    print("刷新基线：.venv/bin/python loadtest/check_baseline.py --update --results " + str(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
