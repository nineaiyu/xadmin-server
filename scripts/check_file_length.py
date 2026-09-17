#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_file_length
# author : ly_ix
# date : 2026/09/17
"""源码文件行数静态门禁（防巨型文件回潮）。

口径：

- 扫描主干应用目录下的 Python 源码（与 ``check_cross_app_imports.py`` 同一扫描面）；
- 排除 ``migrations`` / ``tests`` / ``__pycache__`` 等非源码目录；
- 单文件超过阈值（500 行）即视为巨型文件。

存量处理：

- 当前已超阈值的文件登记在 ``BASELINE`` 并记录基线行数；
- 基线文件**只允许缩小、不允许继续增长**（超过登记值即失败）；
- 已降到阈值以内的基线项在报告中提示可移除；
- 未登记的新增超阈值文件直接失败（防止新巨型文件出现）。

用法::

    python scripts/check_file_length.py            # 门禁（CI 用）
    python scripts/check_file_length.py --report   # 仅报告全量分布，恒不失败

新增违例时退出码 1（CI 阻断）。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 与 check_cross_app_imports.py 保持同一扫描面
SCAN_DIRS = sorted({"common", "system", "notifications", "message", "settings", "captcha", "mfa", "demo"}) + [
    "utils",
    "server",
]

EXCLUDED_SEGMENTS = {"migrations", "tests", "__pycache__", ".venv", "node_modules"}

THRESHOLD = 500

# 存量基线：path -> 基线行数（只减不增；行数降到阈值内即可从此表移除）
# 2026-09-17：存量巨型文件已全部拆分清零（历史 17 处 → 0），保留空表以承接未来回归
BASELINE = {}


def iter_sources():
    for dirname in SCAN_DIRS:
        root = REPO_ROOT / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(segment in EXCLUDED_SEGMENTS for segment in path.parts):
                continue
            yield path


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())


def collect() -> list:
    rows = []
    for path in iter_sources():
        lines = count_lines(path)
        rows.append((relative(path), lines))
    return rows


def main() -> int:
    report_only = "--report" in sys.argv[1:]
    rows = collect()
    oversized = sorted((row for row in rows if row[1] > THRESHOLD), key=lambda item: item[1], reverse=True)

    print(f"文件行数门禁：扫描 {len(rows)} 个源文件，阈值 {THRESHOLD} 行，超阈值 {len(oversized)} 个。")
    for rel, lines in oversized:
        flag = ""
        if rel in BASELINE:
            delta = lines - BASELINE[rel]
            if delta > 0:
                note = f"超基线 +{delta}"
            elif delta < 0:
                note = f"降 {-delta}"
            else:
                note = "维持"
            flag = f"（存量基线 {BASELINE[rel]}，{note}）"
        print(f"  {lines:>5} 行  {rel}{flag}")

    if report_only:
        return 0

    violations = []
    for rel, lines in oversized:
        if rel not in BASELINE:
            violations.append(f"{rel}: {lines} 行（未登记的新增巨型文件）")
            continue
        if lines > BASELINE[rel]:
            violations.append(f"{rel}: {lines} 行（超过存量基线 {BASELINE[rel]}，只减不增）")

    stopped = [rel for rel, lines in oversized if rel in BASELINE and lines <= THRESHOLD]
    if stopped:
        print("以下文件已降到阈值内，可从 BASELINE 移除：")
        for rel in sorted(stopped):
            print(f"  {rel}")

    if violations:
        print("\n巨型文件门禁失败（禁止新增 / 存量禁止增长；确需保留请拆分或更新基线说明）：")
        for item in violations:
            print(f"  {item}")
        return 1

    print(f"巨型文件门禁通过（存量基线 {len(BASELINE)} 项，均未增长）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
