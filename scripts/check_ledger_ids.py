#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""台账编号静态门禁：禁止 ``ADR-xxx`` / 任务编号进入源码与配置。

口径（与 AGENTS.md「任务编号不进版本库」一致）：

- 源码注释 / docstring / 测试名 / 种子 JSON / 配置文件里不写台账编号；
- 台账编号只允许出现在提交信息、``docs/``（含 ``docs/plans/`` 与 ``docs/adr/``）
  以及本脚本与 ``check_adr_status.py`` 的实现/自测中；
- 禁止形态：``ADR-090``、``T03-01``、``O10-2``、``TG-3`` 一类。

用法::

    python scripts/check_ledger_ids.py            # 门禁（CI 用）
    python scripts/check_ledger_ids.py --report   # 仅报告，恒不失败

新增违例时退出码 1（CI 阻断）。
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 扫描的源码/配置后缀（文档类后缀不扫：docs/ 与本仓 README 允许台账引用）
SCAN_SUFFIXES = {
    ".py",
    ".sh",
    ".bash",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".js",
    ".mjs",
    ".ts",
    ".vue",
    ".scss",
    ".css",
    ".po",
    ".pot",
}

#: 台账编号形态（ADR 决策编号 + 任务/评审台账编号）
LEDGER_PATTERNS = (
    re.compile(r"ADR-\d+"),
    re.compile(r"\bT\d{2,}-\d{2,}\b"),
    re.compile(r"\bO\d{1,2}-\d+\b"),
    re.compile(r"\bTG-\d+\b"),
)

#: 非源码目录
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "docs",
    "htmlcov",
}

#: 允许保留台账编号的文件（门禁实现与自测、ADR 状态检查器）
EXEMPT_FILES = {
    "scripts/check_ledger_ids.py",
    "scripts/check_adr_status.py",
    "tests/unit/scripts/test_gate_scripts.py",
    # AI 检索评测集：expected 是 docs/adr/ 下的真实文档路径，属文档引用而非台账标记
    "tests/data/ai_retrieval_eval.json",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
}


def iter_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        if path.relative_to(REPO_ROOT).as_posix() in EXEMPT_FILES:
            continue
        files.append(path)
    return files


def main() -> int:
    report_only = "--report" in sys.argv
    violations = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in LEDGER_PATTERNS:
                match = pattern.search(line)
                if match:
                    violations.append((path.relative_to(REPO_ROOT).as_posix(), lineno, match.group(0), line.strip()))
                    break

    if not violations:
        print("[ledger-ids] 未发现台账编号进入源码/配置")
        return 0

    print(f"[ledger-ids] 发现 {len(violations)} 处台账编号（应只出现在提交信息与 docs/ 台账本体）：")
    for path, lineno, token, line in violations:
        print(f"  {path}:{lineno}: {token} | {line[:120]}")
    if report_only:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
