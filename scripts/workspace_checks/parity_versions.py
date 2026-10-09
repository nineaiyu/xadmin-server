# -*- coding: utf-8 -*-
"""版本矩阵同源校验：服务端 VERSION 为真源，其余副本必须一致。

副本：``xadmin-client/package.json`` 的 version、``xadmin-docs/guide/demo.md`` 的一键安装
``VERSION=vX.Y.Z``、``xadmin-installer/static.env`` 的 ``VERSION``（``dev`` 属开发豁免）。
归一化：去可选前导 ``v``。任一副本与服务端不一致即 fail。
"""

from __future__ import annotations

from . import report, textutil

FACE = "versions"
REPOS = ("xadmin-server", "xadmin-client", "xadmin-docs", "xadmin-installer")

SERVER_VERSION_FILE = "server/const.py"
CLIENT_VERSION_FILE = "package.json"
DOCS_VERSION_FILE = "guide/demo.md"
INSTALLER_VERSION_FILE = "static.env"
DEV_EXEMPT = "dev"


def run(ws, tier: str = "fast") -> list:
    results: list = []
    server = ws.path("xadmin-server")
    if server is None:
        results.append(report.repo_gap(FACE, "xadmin-server", ws.is_allowed("xadmin-server")))
        return results

    const_file = server / SERVER_VERSION_FILE
    if not const_file.is_file():
        results.append(
            report.CheckResult(
                FACE, "server-version", report.STATUS_FAIL, findings=[f"未找到 {const_file}"], repo="xadmin-server"
            )
        )
        return results
    baseline = textutil.normalize_version(textutil.parse_python_version(const_file.read_text(encoding="utf-8")))
    if not baseline:
        results.append(
            report.CheckResult(
                FACE,
                "server-version",
                report.STATUS_FAIL,
                findings=[f"{SERVER_VERSION_FILE} 未解析到 VERSION"],
                repo="xadmin-server",
            )
        )
        return results
    results.append(
        report.CheckResult(
            FACE,
            "server-version",
            report.STATUS_PASS,
            evidence=[f"{SERVER_VERSION_FILE}: {baseline}（真源）"],
            repo="xadmin-server",
        )
    )

    results.append(_compare(ws, "xadmin-client", CLIENT_VERSION_FILE, baseline, textutil.parse_json_version))
    results.append(_compare(ws, "xadmin-docs", DOCS_VERSION_FILE, baseline, textutil.parse_shell_env_version))
    results.append(_compare(ws, "xadmin-installer", INSTALLER_VERSION_FILE, baseline, textutil.parse_shell_env_version))
    return results


def _compare(ws, repo: str, rel: str, baseline: str, parser) -> report.CheckResult:
    path = ws.path(repo)
    if path is None:
        return report.repo_gap(FACE, repo, ws.is_allowed(repo))
    target = path / rel
    if not target.is_file():
        return report.CheckResult(
            FACE, f"version:{repo}", report.STATUS_FAIL, findings=[f"未找到版本文件 {target}"], repo=repo
        )
    raw = parser(target.read_text(encoding="utf-8"))
    value = textutil.normalize_version(raw)
    if value is None:
        return report.CheckResult(
            FACE, f"version:{repo}", report.STATUS_FAIL, findings=[f"{rel} 未解析到版本串"], repo=repo
        )
    if repo == "xadmin-installer" and value == DEV_EXEMPT:
        return report.CheckResult(
            FACE,
            f"version:{repo}",
            report.STATUS_PASS,
            note=f"{rel} VERSION=dev 属开发豁免，不计漂移",
            evidence=[f"{repo}/{rel}: dev"],
            repo=repo,
        )
    if value != baseline:
        return report.CheckResult(
            FACE,
            f"version:{repo}",
            report.STATUS_FAIL,
            findings=[f"{repo} 版本 {value} 与服务端 {baseline} 不一致（服务端为真源）"],
            evidence=[f"{repo}/{rel}: {raw}"],
            repo=repo,
        )
    return report.CheckResult(
        FACE,
        f"version:{repo}",
        report.STATUS_PASS,
        evidence=[f"{repo}/{rel}: {value}"],
        repo=repo,
    )
