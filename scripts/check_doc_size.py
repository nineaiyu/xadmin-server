#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : check_doc_size
# author : ly_ix
# date : 2026/10/09
"""文档体积预算静态门禁（防单篇文档巨型化）。

口径：

- 扫描 ``docs/`` 下的全部 markdown（``*.md``，递归）；
- 单篇文档超过预算（``DOC_SIZE_BUDGET_KB`` KB，UTF-8 字节数）即视为巨型文档。

存量处理（与 ``check_file_length.py`` 同范式）：

- 只读历史归档整体按目录前缀豁免（``DOC_SIZE_EXEMPT_PREFIXES``，逐项写明理由）；
- 需保留的超预算单篇登记在 ``DOC_SIZE_EXEMPT``（附理由与基线），**只减不增**：
  超过登记基线即失败，降到预算内即提示可移除；
- 未登记的新增超预算文档直接失败（防新巨型文档出现）。

用法::

    python scripts/check_doc_size.py            # 门禁（CI lint.yml doc-facts job）
    python scripts/check_doc_size.py --report   # 仅报告全量分布，恒不失败

新增违例时退出码 1（CI 阻断）。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 扫描面：docs/ 下全部 markdown（递归）
DOC_GLOB = "docs/**/*.md"

# 单篇文档体积预算（KB，UTF-8 字节数）
DOC_SIZE_BUDGET_KB = 40

# 目录前缀豁免：rel 以此开头即整体豁免；值 = 理由（整段只读、不再增长）
DOC_SIZE_EXEMPT_PREFIXES = {
    "docs/plans/archive/": "只读历史归档：已完成的一次性战役台账全文，仅作制度记忆与追溯，不再增长",
}

# 单篇豁免：rel -> {"reason": 理由, "baseline_kb": 基线 KB}（只减不增）
DOC_SIZE_EXEMPT = {}


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def iter_docs():
    yield from sorted(REPO_ROOT.glob(DOC_GLOB))


def size_kb(path: Path) -> float:
    return len(path.read_text(encoding="utf-8").encode("utf-8")) / 1024.0


def _prefix_reason(rel: str):
    for prefix, reason in DOC_SIZE_EXEMPT_PREFIXES.items():
        if rel.startswith(prefix):
            return reason
    return None


def collect() -> list:
    """返回 ``[(rel, kb), ...]``（与路径字典序一致）。"""
    return [(_relative(path), size_kb(path)) for path in iter_docs()]


def main() -> int:
    report_only = "--report" in sys.argv[1:]
    rows = collect()
    oversized = sorted((row for row in rows if row[1] > DOC_SIZE_BUDGET_KB), key=lambda item: item[1], reverse=True)

    print(f"文档体积门禁：扫描 {len(rows)} 篇文档，预算 {DOC_SIZE_BUDGET_KB} KB，超预算 {len(oversized)} 篇。")
    for rel, kb in oversized:
        flag = ""
        if _prefix_reason(rel):
            flag = "（目录前缀豁免：只读归档）"
        elif rel in DOC_SIZE_EXEMPT:
            baseline = DOC_SIZE_EXEMPT[rel]["baseline_kb"]
            delta = kb - baseline
            note = f"超基线 +{delta:.2f}" if delta > 0 else (f"降 {-delta:.2f}" if delta < 0 else "维持")
            flag = f"（单篇豁免基线 {baseline} KB，{note}）"
        print(f"  {kb:>7.2f} KB  {rel}{flag}")

    if report_only:
        return 0

    violations = []
    for rel, kb in oversized:
        if _prefix_reason(rel):
            continue
        if rel in DOC_SIZE_EXEMPT:
            baseline = DOC_SIZE_EXEMPT[rel]["baseline_kb"]
            if kb > baseline:
                violations.append(f"{rel}: {kb:.2f} KB（超过单篇豁免基线 {baseline} KB，只减不增）")
            continue
        violations.append(f"{rel}: {kb:.2f} KB（未登记的新增超预算文档）")

    stopped = [rel for rel, kb in oversized if rel in DOC_SIZE_EXEMPT and kb <= DOC_SIZE_BUDGET_KB]
    if stopped:
        print("以下文档已降到预算内，可从 DOC_SIZE_EXEMPT 移除：")
        for rel in sorted(stopped):
            print(f"  {rel}")

    if violations:
        print("\n文档体积门禁失败（禁止新增超预算文档；确需保留请拆分或登记豁免并说明理由）：")
        for item in violations:
            print(f"  {item}")
        return 1

    print(
        f"文档体积门禁通过（预算 {DOC_SIZE_BUDGET_KB} KB，豁免前缀 {len(DOC_SIZE_EXEMPT_PREFIXES)} 段 / 单篇 {len(DOC_SIZE_EXEMPT)} 篇）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
