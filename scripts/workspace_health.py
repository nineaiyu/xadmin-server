#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作区级跨仓一致性只读校验（薄入口）。

把「同一份事实在多个仓库中的副本是否一致」集中到一个可复跑的口子：CSP 策略 /
权限种子 / 契约 schema / 国际化词条 / 版本矩阵 / 单仓门禁。全部只读——只读取被检查
文件，绝不改写（写盘档位默认关闭）。单仓门禁的 ``[skip]``（缺兄弟仓）在单仓 CI 是现实，
本脚本是真跑口：缺仓一律 missing，只有 ``--allow-missing`` 才降级 degraded。

退出码：0 全绿 / 1 有 fail / 2 有 missing 未放行。

用法::

    python scripts/workspace_health.py                       # fast 档，工作区 = 上级目录
    python scripts/workspace_health.py --only csp,versions
    python scripts/workspace_health.py --tier full --allow-write --format md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from workspace_checks import (  # noqa: E402
    discovery,
    gate_runner,
    parity_contract,
    parity_csp,
    parity_locale,
    parity_permissions,
    parity_versions,
    report,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

FACE_MODULES = {
    "csp": parity_csp,
    "permissions": parity_permissions,
    "contract": parity_contract,
    "locale": parity_locale,
    "versions": parity_versions,
}
FACE_ORDER = ("csp", "permissions", "contract", "locale", "versions", "gates")
ALL_FACES = tuple(FACE_ORDER)


def _parse_csv(value: str) -> set:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="工作区级跨仓一致性只读校验")
    parser.add_argument("--workspace-root", default=str(REPO_ROOT.parent), help="工作区根（默认本仓上级目录）")
    parser.add_argument(
        "--allow-missing",
        default=",".join(discovery.DEFAULT_ALLOW_MISSING),
        help="放行的缺失仓（逗号分隔），缺仓降级 degraded 而非 missing",
    )
    parser.add_argument("--only", default="", help="仅跑指定面（逗号分隔）")
    parser.add_argument("--skip", default="", help="跳过指定面（逗号分隔）")
    parser.add_argument(
        "--tier", choices=("fast", "full"), default="fast", help="档位：fast 仅静态门禁；full 追加重门禁"
    )
    parser.add_argument("--allow-write", action="store_true", help="允许写盘门禁（仅 tier=full 生效）")
    parser.add_argument("--json", default="", help="额外把 JSON 报告写入该路径")
    parser.add_argument("--format", choices=("text", "md", "json"), default="text")
    return parser


def select_faces(args) -> list:
    only = _parse_csv(args.only)
    skip = _parse_csv(args.skip)
    unknown = (only | skip) - set(ALL_FACES)
    if unknown:
        raise SystemExit(f"未知的校验面：{', '.join(sorted(unknown))}（可用：{', '.join(ALL_FACES)}）")
    selected = [face for face in FACE_ORDER if face not in skip]
    if only:
        selected = [face for face in selected if face in only]
    return selected


def face_specs(faces: list) -> list:
    specs = []
    for face in faces:
        repos = gate_runner.REPOS if face == "gates" else FACE_MODULES[face].REPOS
        specs.append(report.FaceSpec(face, repos))
    return specs


def run_faces(faces: list, found, tier: str, allow_write: bool) -> list:
    results: list = []
    for face in faces:
        try:
            if face == "gates":
                produced = gate_runner.run(found, tier=tier, allow_write=allow_write)
            else:
                produced = FACE_MODULES[face].run(found, tier=tier)
        except Exception as exc:  # 面级异常归一为 fail（禁止 try/except → skip）
            produced = [
                report.CheckResult(
                    face,
                    "face-error",
                    report.STATUS_FAIL,
                    findings=[f"{face} 面执行异常：{type(exc).__name__}: {exc}"],
                )
            ]
        results.extend(produced)
    return results


def _print_preflight(specs: list, found) -> None:
    """运行前骨架与告警写 stderr：stdout 留给报告本体（`--format json` 时保持纯 JSON）。"""
    print("# 工作区健康报告（运行前骨架）", file=sys.stderr)
    print(f"工作区根：{found.root}", file=sys.stderr)
    present = ", ".join(repo for repo in discovery.ALL_REPOS if found.is_present(repo)) or "（无）"
    print(f"检出仓：{present}", file=sys.stderr)
    print(f"缺失仓：{', '.join(found.missing) or '（无）'}", file=sys.stderr)
    print("面 × 仓 矩阵（运行前骨架）：", file=sys.stderr)
    print("  面 | " + " | ".join(discovery.ALL_REPOS), file=sys.stderr)
    for spec in specs:
        cells = [repo if repo in spec.repos else "-" for repo in discovery.ALL_REPOS]
        print(f"  {spec.name} | " + " | ".join(cells), file=sys.stderr)


def _suggestions(found, results: list) -> list:
    items = []
    for result in results:
        if result.status == report.STATUS_FAIL:
            items.append(f"[{result.face}] 修复 {result.id}" + (f"（{result.repo}）" if result.repo else ""))
    not_allowed = discovery.missing_not_allowed(found)
    if not_allowed:
        items.append("检出缺失仓，或显式 --allow-missing 放行：" + ", ".join(not_allowed))
    return items


def _warnings(found) -> list:
    warnings = []
    if "xadmin-web" in found.missing and found.is_allowed("xadmin-web"):
        warnings.append("xadmin-web 未检出 → 未覆盖 CSP 页面层（nginx 强制头）")
    return warnings


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    found = discovery.discover(Path(args.workspace_root), allow_missing=_parse_csv(args.allow_missing))
    faces = select_faces(args)
    specs = face_specs(faces)

    _print_preflight(specs, found)
    results = run_faces(faces, found, args.tier, args.allow_write)

    # 空格检测：适用仓在结果集中无任何条目即视为校验漏洞（fail），并回填矩阵使报告如实显示。
    blanks = report.build_matrix(specs, discovery.ALL_REPOS, results).blanks()
    for face, repo in blanks:
        results.append(
            report.CheckResult(
                face,
                "matrix",
                report.STATUS_FAIL,
                findings=[f"面 {face} × 仓 {repo} 未产生结果（矩阵空格，校验漏洞）"],
                repo=repo,
            )
        )
    matrix = report.build_matrix(specs, discovery.ALL_REPOS, results)

    checked_out = [repo for repo in discovery.ALL_REPOS if found.is_present(repo)]
    health = report.HealthReport(
        workspace_root=str(found.root),
        checked_out=checked_out,
        missing=list(found.missing),
        allowed_missing=sorted(found.allowed_missing),
        results=results,
        matrix=matrix,
        suggestions=_suggestions(found, results),
        warnings=_warnings(found),
        blocking_missing=discovery.missing_not_allowed(found),
    )
    for item in health.warnings:
        print(f"[warn] {item}", file=sys.stderr)

    if args.json:
        Path(args.json).write_text(health.to_json(), encoding="utf-8")
    print(report.render(health, args.format))
    return health.exit_code()


if __name__ == "__main__":
    sys.exit(main())
