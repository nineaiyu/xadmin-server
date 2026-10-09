# -*- coding: utf-8 -*-
"""CSP 策略三处同源校验：服务端定义 ↔ 页面层 nginx ↔ 隔离验证服务。

真源：``xadmin-server/server/settings/csp.py`` 的 ``_CSP_DIRECTIVES``；
副本：``xadmin-web/default.conf`` 的强制头、``xadmin-client/scripts/csp-page-server.mjs``
内嵌串。任一处漂移都会让「隔离验证通过」与线上实际策略不一致。
xadmin-web 不入 git，默认放行（degraded）并在报告标注「未覆盖 CSP 页面层」。
"""

from __future__ import annotations

from . import report, textutil

FACE = "csp"
REPOS = ("xadmin-server", "xadmin-web", "xadmin-client")

SERVER_CSP = "server/settings/csp.py"
WEB_CONF = "default.conf"
CLIENT_HARNESS = "scripts/csp-page-server.mjs"
WEB_LAYER_NOTE = "未覆盖 CSP 页面层（xadmin-web 不在依赖检出面内）"


def _rel(path, root) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def run(ws, tier: str = "fast") -> list:
    results: list = []
    server = ws.path("xadmin-server")
    if server is None:
        return [report.repo_gap(FACE, "xadmin-server", ws.is_allowed("xadmin-server"))]

    csp_file = server / SERVER_CSP
    if not csp_file.is_file():
        return [
            report.CheckResult(
                FACE, "server-definition", report.STATUS_FAIL, findings=[f"未找到 {csp_file}"], repo="xadmin-server"
            )
        ]
    try:
        directives = textutil.parse_csp_directives(csp_file.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError) as exc:
        return [
            report.CheckResult(
                FACE,
                "server-definition",
                report.STATUS_FAIL,
                findings=[f"解析 _CSP_DIRECTIVES 失败：{exc}"],
                repo="xadmin-server",
            )
        ]
    expected = textutil.csp_expectation(directives)
    results.append(
        report.CheckResult(
            FACE,
            "server-definition",
            report.STATUS_PASS,
            evidence=[f"{_rel(csp_file, ws.root)}:{len(directives)} 组指令（真源）"],
            repo="xadmin-server",
        )
    )

    web = ws.path("xadmin-web")
    if web is None:
        results.append(report.repo_gap(FACE, "xadmin-web", ws.is_allowed("xadmin-web"), note=WEB_LAYER_NOTE))
    else:
        results.append(
            _check_place(FACE, "web-layer", web / WEB_CONF, textutil.parse_nginx_csp, expected, ws, "xadmin-web")
        )

    client = ws.path("xadmin-client")
    if client is None:
        results.append(report.repo_gap(FACE, "xadmin-client", ws.is_allowed("xadmin-client")))
    else:
        results.append(
            _check_place(
                FACE, "client-harness", client / CLIENT_HARNESS, textutil.parse_mjs_csp, expected, ws, "xadmin-client"
            )
        )
    return results


def _check_place(face, check_id, path, parser, expected, ws, repo) -> report.CheckResult:
    """解析某副本的策略串并与真源归一化集合比较。"""
    if not path.is_file():
        return report.CheckResult(face, check_id, report.STATUS_FAIL, findings=[f"未找到副本文件 {path}"], repo=repo)
    parsed = parser(path.read_text(encoding="utf-8"))
    if parsed is None:
        return report.CheckResult(
            face, check_id, report.STATUS_FAIL, findings=[f"{_rel(path, ws.root)} 未找到 CSP 策略串定义"], repo=repo
        )
    policy, line = parsed
    actual = textutil.split_policy(policy)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        findings = []
        if missing:
            findings.append(f"缺少/不一致指令：{missing}")
        if extra:
            findings.append(f"多出指令：{extra}")
        return report.CheckResult(
            face,
            check_id,
            report.STATUS_FAIL,
            findings=findings or ["策略串与真源漂移"],
            evidence=[f"{_rel(path, ws.root)}:{line}"],
            repo=repo,
        )
    return report.CheckResult(
        face,
        check_id,
        report.STATUS_PASS,
        evidence=[f"{_rel(path, ws.root)}:{line} 与真源逐指令一致"],
        repo=repo,
    )
