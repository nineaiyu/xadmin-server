#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""函数行数静态门禁（防超长函数回潮，与 check_file_length.py 同口径）。

口径：

- 扫描面与 ``check_file_length.py`` 一致（框架内核 + 主干业务 app，排除 migrations/tests）；
- 以 ``ast`` 计行（``end_lineno - lineno + 1``，含 def 行与装饰器之外的全部函数体）；
- 单函数超过阈值（100 行）即视为超长函数。

存量处理（与文件门禁同构）：

- 当前已超阈值的函数登记在 ``BASELINE``（键 = ``相对路径::限定名``）并记录基线行数；
- 基线函数**只允许缩小、不允许继续增长**（超过登记值即失败）；
- 已降到阈值以内的基线项在报告中提示可移除；
- 未登记的新增超阈值函数直接失败。

用法::

    python scripts/check_function_length.py            # 门禁（CI 用）
    python scripts/check_function_length.py --report   # 仅报告全量分布，恒不失败

新增违例时退出码 1（CI 阻断）。
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 与 check_file_length.py / check_cross_app_imports.py 保持同一扫描面
SCAN_DIRS = sorted(
    {
        "packages/xadmin-common/common",
        "system",
        "notifications",
        "message",
        "settings",
        "captcha",
        "mfa",
        "demo",
        "identity",
        "file",
        "audit",
        "task",
    }
) + [
    "ops",
    "server",
    "integrations",
    "devtools",
    "examples",
]

EXCLUDED_SEGMENTS = {"migrations", "tests", "__pycache__", ".venv", "node_modules"}

THRESHOLD = 100

# 存量基线：`相对路径::限定名` -> 基线行数（只减不增；行数降到阈值内即可从此表移除）
# 2026-10-10 建立：7 项 → 清偿批次一拆分 5 项（metadata_columns / data_scope.compiler /
# task 导入实现 / captcha 绘图 / message ai 流式），余 2 项登记待后续批次。
BASELINE = {
    "devtools/management/commands/_generate_crud/analysis.py::AnalysisMixin._collect_artifacts": 113,
    "identity/utils/account_risk.py::_collect_risks": 111,
}


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


def _qualname(node: ast.AST, parents: list[ast.AST]) -> str:
    names = [
        node.name
        for node in [*parents, node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return ".".join(names)


def collect() -> list:
    """返回 [(相对路径, 限定名, 行数)]，仅含达到阈值的函数。"""
    rows: list = []
    for path in iter_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        visit(tree, [], relative(path), rows)
    return rows


def visit(node: ast.AST, parents: list, rel: str, rows: list) -> None:
    """深度优先遍历，收集超阈值函数（嵌套函数 / 方法以限定名区分）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(child, "end_lineno", None)
            if end is not None:
                size = end - child.lineno + 1
                if size >= THRESHOLD:
                    rows.append((rel, _qualname(child, parents), size))
            visit(child, [*parents, child], rel, rows)
        elif isinstance(child, ast.ClassDef):
            visit(child, [*parents, child], rel, rows)
        else:
            visit(child, parents, rel, rows)


def main() -> int:
    report_only = "--report" in sys.argv[1:]
    rows = sorted(collect(), key=lambda item: item[2], reverse=True)

    print(f"函数行数门禁：阈值 {THRESHOLD} 行，超阈值 {len(rows)} 个。")
    for rel, name, lines in rows:
        key = f"{rel}::{name}"
        flag = ""
        if key in BASELINE:
            delta = lines - BASELINE[key]
            note = f"超基线 +{delta}" if delta > 0 else (f"降 {-delta}" if delta < 0 else "维持")
            flag = f"（存量基线 {BASELINE[key]}，{note}）"
        print(f"  {lines:>4} 行  {rel}::{name}{flag}")

    if report_only:
        return 0

    violations = []
    for rel, name, lines in rows:
        key = f"{rel}::{name}"
        if key not in BASELINE:
            violations.append(f"{key}: {lines} 行（未登记的新增超长函数）")
            continue
        if lines > BASELINE[key]:
            violations.append(f"{key}: {lines} 行（超过存量基线 {BASELINE[key]}，只减不增）")

    stopped = [key for _rel, _name, lines in rows if (key := f"{_rel}::{_name}") in BASELINE and lines < THRESHOLD]
    if stopped:
        print("以下函数已降到阈值内，可从 BASELINE 移除：")
        for key in sorted(stopped):
            print(f"  {key}")

    if violations:
        print("\n函数行数门禁失败（禁止新增 / 存量禁止增长；确需保留请拆分或更新基线说明）：")
        for item in violations:
            print(f"  {item}")
        return 1

    print(f"函数行数门禁通过（存量基线 {len(BASELINE)} 项，均未增长）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
