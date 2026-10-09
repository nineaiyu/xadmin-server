# -*- coding: utf-8 -*-
"""工作区健康报告的数据结构、矩阵与渲染（text / md / json）。

状态语义：

- ``pass``     该检查通过；
- ``fail``     检出真实漂移/违例（退出码 1）；
- ``missing``  依赖的仓库/文件未检出且未被 ``--allow-missing`` 放行（退出码 2）；
- ``degraded`` 依赖缺失但已显式放行，或工具不可用/写盘档位未开启——不阻断但需人工知悉；
- ``n/a``      该面在该仓无适用项（矩阵格，非空白）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_MISSING = "missing"
STATUS_DEGRADED = "degraded"
STATUS_NA = "n/a"

_STATUS_ORDER = {
    STATUS_FAIL: 0,
    STATUS_MISSING: 1,
    STATUS_DEGRADED: 2,
    STATUS_PASS: 3,
    STATUS_NA: 4,
}

_STATUS_LABEL = {
    STATUS_PASS: "通过",
    STATUS_FAIL: "失败",
    STATUS_MISSING: "缺失",
    STATUS_DEGRADED: "降级",
    STATUS_NA: "不适用",
}


@dataclass
class CheckResult:
    """单条检查结果。``repo`` 为空表示面级（跨仓）检查。"""

    face: str
    id: str
    status: str
    findings: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    seconds: float = 0.0
    repo: str | None = None
    note: str = ""


@dataclass
class FaceSpec:
    """面的适用仓集合（用于矩阵：面 × 仓）。"""

    name: str
    repos: tuple = ()


@dataclass
class Matrix:
    """面 × 仓 状态矩阵；未被显式填充的格视为「空格」（校验漏洞）。"""

    faces: list
    repos: list
    cells: dict = field(default_factory=dict)

    def set(self, face: str, repo: str, status: str) -> None:
        self.cells[(face, repo)] = status

    def get(self, face: str, repo: str) -> str | None:
        return self.cells.get((face, repo))

    def blanks(self) -> list:
        return [(face, repo) for face in self.faces for repo in self.repos if self.cells.get((face, repo)) is None]


def worst(statuses) -> str:
    """取状态集合中最差者（无输入返回 pass）。"""
    ranked = sorted((s for s in statuses if s), key=lambda s: _STATUS_ORDER.get(s, 9))
    return ranked[0] if ranked else STATUS_PASS


def repo_gap(face: str, repo: str, allowed: bool, note: str = "") -> CheckResult:
    """依赖仓缺失的统一结果：未放行 → missing；已放行 → degraded。"""
    status = STATUS_DEGRADED if allowed else STATUS_MISSING
    suffix = "（已放行，降级为 degraded）" if allowed else "（未放行）"
    detail = f"仓库 {repo} 未检出{suffix}"
    if note:
        detail = f"{detail}；{note}"
    return CheckResult(face=face, id=f"repo:{repo}", status=status, findings=[detail], repo=repo)


def build_matrix(specs: list, repos: list, results: list) -> Matrix:
    """由「面的适用仓声明」+ 结果集构建矩阵，并填充 n/a 格。"""
    matrix = Matrix(faces=[spec.name for spec in specs], repos=list(repos))
    for spec in specs:
        applicable = set(spec.repos) & set(repos)
        for repo in repos:
            if repo not in applicable:
                matrix.set(spec.name, repo, STATUS_NA)
                continue
            statuses = [r.status for r in results if r.face == spec.name and r.repo == repo]
            if statuses:
                matrix.set(spec.name, repo, worst(statuses))
    return matrix


@dataclass
class HealthReport:
    workspace_root: str
    checked_out: list
    missing: list
    allowed_missing: list
    results: list
    matrix: Matrix
    suggestions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    blocking_missing: list = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    def face_status(self) -> dict:
        faces = sorted({r.face for r in self.results})
        return {face: worst([r.status for r in self.results if r.face == face]) for face in faces}

    def counts(self) -> dict:
        tally = dict.fromkeys(_STATUS_ORDER, 0)
        for result in self.results:
            tally[result.status] = tally.get(result.status, 0) + 1
        return tally

    def exit_code(self) -> int:
        statuses = {r.status for r in self.results}
        if STATUS_FAIL in statuses:
            return 1
        if STATUS_MISSING in statuses or self.blocking_missing:
            return 2
        return 0

    def to_dict(self) -> dict:
        return {
            "workspace_root": self.workspace_root,
            "generated_at": self.generated_at,
            "checked_out": self.checked_out,
            "missing": self.missing,
            "allowed_missing": self.allowed_missing,
            "face_status": self.face_status(),
            "counts": self.counts(),
            "matrix": {
                "faces": self.matrix.faces,
                "repos": self.matrix.repos,
                "cells": [
                    {"face": f, "repo": r, "status": self.matrix.get(f, r)}
                    for f in self.matrix.faces
                    for r in self.matrix.repos
                ],
            },
            "results": [
                {
                    "face": r.face,
                    "id": r.id,
                    "repo": r.repo,
                    "status": r.status,
                    "seconds": round(r.seconds, 3),
                    "findings": r.findings,
                    "evidence": r.evidence,
                    "note": r.note,
                }
                for r in self.results
            ],
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "blocking_missing": self.blocking_missing,
            "exit_code": self.exit_code(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _detail_lines(result: CheckResult) -> list:
    lines = [f"[{result.status}] {result.face} / {result.id}" + (f" @ {result.repo}" if result.repo else "")]
    if result.note:
        lines.append(f"      说明：{result.note}")
    for item in result.findings:
        lines.append(f"      发现：{item}")
    for item in result.evidence:
        lines.append(f"      证据：{item}")
    return lines


def render_text(report: HealthReport) -> str:
    """控制台阅读形态。"""
    out = ["# 工作区健康报告", ""]
    out.append(f"生成时间：{report.generated_at}")
    out.append(f"工作区根：{report.workspace_root}")
    out.append(f"检出仓：{', '.join(report.checked_out) or '（无）'}")
    missing = ", ".join(report.missing) or "（无）"
    out.append(f"缺失仓：{missing}")
    out.append(f"放行缺失：{', '.join(report.allowed_missing) or '（无）'}")
    out.append("")
    out.append("## 总览")
    tally = report.counts()
    out.append(
        f"面 {len(report.face_status())} 个 / 检查 {len(report.results)} 条："
        f"pass {tally.get(STATUS_PASS, 0)}，fail {tally.get(STATUS_FAIL, 0)}，"
        f"missing {tally.get(STATUS_MISSING, 0)}，degraded {tally.get(STATUS_DEGRADED, 0)}，"
        f"n/a {tally.get(STATUS_NA, 0)}"
    )
    out.append("")
    out.append("## 面 × 仓 矩阵")
    header = ["面"] + report.matrix.repos
    out.append("  " + " | ".join(header))
    for face in report.matrix.faces:
        row = [face] + [str(report.matrix.get(face, repo)) for repo in report.matrix.repos]
        out.append("  " + " | ".join(row))
    out.append("")
    out.append("## 明细")
    for result in report.results:
        out.extend(_detail_lines(result))
    out.append("")
    out.append("## 建议动作")
    for item in report.suggestions or ["无"]:
        out.append(f"- {item}")
    for item in report.warnings:
        out.append(f"! {item}")
    out.append("")
    out.append(f"退出码：{report.exit_code()}（0 全绿 / 1 有 fail / 2 有 missing 未放行）")
    return "\n".join(out)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_md(report: HealthReport) -> str:
    """Markdown 形态（用于 CI issue 正文）。"""
    out = ["# 工作区健康报告", ""]
    out.append(f"- 生成时间：{report.generated_at}")
    out.append(f"- 工作区根：`{report.workspace_root}`")
    out.append(f"- 检出仓：{', '.join(report.checked_out) or '（无）'}")
    out.append(f"- 缺失仓：{', '.join(report.missing) or '（无）'}")
    out.append(f"- 放行缺失：{', '.join(report.allowed_missing) or '（无）'}")
    out.append("")
    out.append("## 总览")
    out.append("| 面 | 状态 |")
    out.append("|---|---|")
    for face, status in report.face_status().items():
        out.append(f"| {face} | {status} |")
    tally = report.counts()
    out.append("")
    out.append(
        f"检查 {len(report.results)} 条：pass {tally.get(STATUS_PASS, 0)} / "
        f"fail {tally.get(STATUS_FAIL, 0)} / missing {tally.get(STATUS_MISSING, 0)} / "
        f"degraded {tally.get(STATUS_DEGRADED, 0)}"
    )
    out.append("")
    out.append("## 面 × 仓 矩阵")
    out.append("| 面 | " + " | ".join(report.matrix.repos) + " |")
    out.append("|---" * (len(report.matrix.repos) + 1) + "|")
    for face in report.matrix.faces:
        cells = [str(report.matrix.get(face, repo)) for repo in report.matrix.repos]
        out.append(f"| {face} | " + " | ".join(cells) + " |")
    out.append("")
    out.append("## 明细")
    for result in report.results:
        title = f"### {result.face} / {result.id}" + (f" @ {result.repo}" if result.repo else "")
        out.append(title)
        out.append(f"- 状态：{result.status}")
        if result.note:
            out.append(f"- 说明：{_md_cell(result.note)}")
        for item in result.findings:
            out.append(f"- 发现：{_md_cell(item)}")
        for item in result.evidence:
            out.append(f"- 证据：`{_md_cell(item)}`")
        out.append("")
    out.append("## 建议动作")
    for item in report.suggestions or ["无"]:
        out.append(f"- {_md_cell(item)}")
    for item in report.warnings:
        out.append(f"- ⚠ {_md_cell(item)}")
    return "\n".join(out)


RENDERERS = {"text": render_text, "md": render_md}


def render(report: HealthReport, fmt: str) -> str:
    if fmt == "json":
        return report.to_json()
    renderer = RENDERERS.get(fmt)
    if renderer is None:  # pragma: no cover - argparse 已约束枚举
        raise ValueError(f"未知报告格式：{fmt}")
    return renderer(report)
