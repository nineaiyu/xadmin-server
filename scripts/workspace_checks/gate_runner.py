# -*- coding: utf-8 -*-
"""单仓门禁聚合执行（fast 默认；full 才跑重档）。

- 声明式 ``GATES`` 表：每条门禁登记 所在仓 / 命令 / 档位 / 是否写盘；
- ``writes_disk=True`` 的门禁（构建 / 端到端）只在 ``tier=full`` 且显式 ``--allow-write`` 时执行；
- 依赖仓库缺失 → missing（未放行）或 degraded（已放行），逐条登记不静默；
- 跨仓门禁经环境变量（``XADMIN_SERVER_DIR`` 等）注入兄弟仓路径。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import procs, report

FACE = "gates"
REPOS = ("xadmin-server", "xadmin-client", "xadmin-installer")

# 供应商内嵌脚本（acme.sh）不纳入 bash 语法检查面
BASH_EXCLUDE = ("acme.sh",)


@dataclass
class Gate:
    id: str
    repo: str
    cmd: list
    tier: str = "fast"
    kind: str = "cmd"
    writes_disk: bool = False
    timeout: float = 900.0


GATES = [
    # ---- xadmin-server：静态门禁（快档） ----
    Gate("cross-app-imports", "xadmin-server", [sys.executable, "scripts/check_cross_app_imports.py"]),
    Gate("file-length", "xadmin-server", [sys.executable, "scripts/check_file_length.py"]),
    Gate("function-length", "xadmin-server", [sys.executable, "scripts/check_function_length.py"]),
    Gate("doc-facts", "xadmin-server", [sys.executable, "scripts/check_doc_facts.py"]),
    Gate("doc-index", "xadmin-server", [sys.executable, "scripts/check_doc_index.py"]),
    Gate("doc-paths", "xadmin-server", [sys.executable, "scripts/check_doc_paths.py"]),
    Gate("tutorial-mirror", "xadmin-server", [sys.executable, "scripts/check_tutorial_mirror.py"]),
    Gate("cache-keys", "xadmin-server", [sys.executable, "scripts/check_cache_keys.py", "--strict"]),
    Gate("docs-site-nav", "xadmin-server", [sys.executable, "scripts/check_docs_site_nav.py"]),
    # ---- xadmin-client：静态门禁（快档） ----
    Gate("as-unknown", "xadmin-client", ["node", "scripts/check-as-unknown.mjs"]),
    Gate("contract-sync", "xadmin-client", ["node", "scripts/check-contract-sync.mjs"]),
    Gate("contract-usage", "xadmin-client", ["node", "scripts/check-contract-usage.mjs"]),
    Gate("i18n", "xadmin-client", ["node", "scripts/check-i18n-keys.mjs"]),
    Gate("menu-permissions", "xadmin-client", ["node", "scripts/check-menu-permissions.mjs"]),
    Gate("module-cycles", "xadmin-client", ["node", "scripts/check-module-cycles.mjs"]),
    Gate("version", "xadmin-client", ["node", "scripts/check-version-sync.mjs"]),
    Gate("file-length", "xadmin-client", ["node", "scripts/check-file-length.mjs"]),
    Gate("hook-length", "xadmin-client", ["node", "scripts/check-hook-length.mjs"]),
    # ---- xadmin-installer：shell 语法（快档） ----
    Gate("bash-syntax", "xadmin-installer", [], kind="bash_syntax"),
    # ---- full 档：重门禁 ----
    Gate("mypy", "xadmin-server", [sys.executable, "-m", "mypy"], tier="full", timeout=1800.0),
    Gate("pytest", "xadmin-server", [sys.executable, "-m", "pytest", "-n", "auto"], tier="full", timeout=3600.0),
    Gate("typecheck", "xadmin-client", ["pnpm", "run", "typecheck"], tier="full", timeout=1800.0),
    Gate("vitest", "xadmin-client", ["pnpm", "run", "test:run"], tier="full", timeout=1800.0),
    Gate(
        "build-bundle",
        "xadmin-client",
        ["bash", "-lc", "pnpm build && pnpm run check:bundle-size"],
        tier="full",
        writes_disk=True,
        timeout=3600.0,
    ),
    Gate(
        "e2e",
        "xadmin-client",
        ["bash", "-lc", "E2E_API_PORT=18896 pnpm run test:e2e"],
        tier="full",
        writes_disk=True,
        timeout=5400.0,
    ),
]


def run(ws, tier: str = "fast", allow_write: bool = False) -> list:
    results: list = []
    seen_missing: set = set()
    skipped_tier: list = []
    env = ws.env()

    for gate in GATES:
        repo_root = ws.path(gate.repo)
        if repo_root is None:
            if gate.repo not in seen_missing:
                seen_missing.add(gate.repo)
                results.append(report.repo_gap(FACE, gate.repo, ws.is_allowed(gate.repo)))
            continue
        if gate.tier == "full" and tier != "full":
            skipped_tier.append(gate.id)
            continue
        if gate.writes_disk and not allow_write:
            results.append(
                report.CheckResult(
                    FACE,
                    gate.id,
                    report.STATUS_DEGRADED,
                    findings=[f"写盘门禁已跳过（需 --tier full 且显式 --allow-write）：{gate.id}"],
                    repo=gate.repo,
                )
            )
            continue
        if gate.kind == "bash_syntax":
            results.append(_bash_syntax(gate, repo_root))
        else:
            results.append(
                procs.run_gate(
                    FACE,
                    gate.id,
                    gate.cmd,
                    cwd=repo_root,
                    env=env,
                    repo=gate.repo,
                    timeout=gate.timeout,
                    allowed=ws.is_allowed(gate.repo),
                    success_note=f"{gate.id} 通过",
                )
            )
    if skipped_tier:
        results.append(
            report.CheckResult(
                FACE,
                "tier-lite",
                report.STATUS_DEGRADED,
                note=f"fast 档跳过 {len(skipped_tier)} 个 full 门禁（{', '.join(skipped_tier)}）；重档用 --tier full",
            )
        )
    return results


def _bash_syntax(gate: Gate, repo_root: Path) -> report.CheckResult:
    """对仓内全部 .sh 执行 ``bash -n``（语法检查，不写盘）。"""
    scripts = [path for path in sorted(repo_root.rglob("*.sh")) if not any(part in BASH_EXCLUDE for part in path.parts)]
    if not scripts:
        return report.CheckResult(FACE, gate.id, report.STATUS_DEGRADED, findings=["未找到 .sh 文件"], repo=gate.repo)
    failures = []
    seconds = 0.0
    for script in scripts:
        result = procs.run(["bash", "-n", str(script)], cwd=repo_root, timeout=60.0)
        seconds += result.seconds
        if result.tool_missing:
            return report.CheckResult(
                FACE, gate.id, report.STATUS_DEGRADED, findings=["未找到 bash"], repo=gate.repo, seconds=seconds
            )
        if not result.ok:
            failures.append(f"{script.relative_to(repo_root).as_posix()}: {result.stderr.strip() or '语法错误'}")
    if failures:
        return report.CheckResult(FACE, gate.id, report.STATUS_FAIL, findings=failures, repo=gate.repo, seconds=seconds)
    return report.CheckResult(
        FACE,
        gate.id,
        report.STATUS_PASS,
        evidence=[f"{len(scripts)} 个 shell 脚本语法检查通过"],
        repo=gate.repo,
        seconds=seconds,
    )
