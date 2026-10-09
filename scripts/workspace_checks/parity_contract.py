# -*- coding: utf-8 -*-
"""契约 schema 同源校验：服务端真源 ↔ 客户端镜像 + 生成物消费。

真源：``xadmin-server/docs/schema/*.schema.json``（5 份）；镜像：``xadmin-client/contract/schema``。
比对忽略缩进/键序差异（只比语义，与前端 ``check-contract-sync.mjs`` 的 normalize 同口径）；
另跑 ``check-contract-usage.mjs`` 守护「生成契约每份必须有消费方」。
"""

from __future__ import annotations

import json

from . import procs, report

FACE = "contract"
REPOS = ("xadmin-server", "xadmin-client")

SCHEMA_NAMES = ("search-columns", "search-fields", "api-response", "routes-payload", "ws-frame")
SOURCE_DIR = "docs/schema"
MIRROR_DIR = "contract/schema"
USAGE_SCRIPT = "scripts/check-contract-usage.mjs"
SYNC_SCRIPT = "scripts/check-contract-sync.mjs"


def _normalize(path) -> str:
    return json.dumps(json.loads(path.read_text(encoding="utf-8")), sort_keys=True, ensure_ascii=False)


def run(ws, tier: str = "fast") -> list:
    results: list = []
    server = ws.path("xadmin-server")
    client = ws.path("xadmin-client")
    if server is None:
        results.append(report.repo_gap(FACE, "xadmin-server", ws.is_allowed("xadmin-server")))
    if client is None:
        results.append(report.repo_gap(FACE, "xadmin-client", ws.is_allowed("xadmin-client")))
    if server is None or client is None:
        return results

    results.append(_server_source(server))
    results.append(_mirror_compare(ws, server, client))
    results.append(
        procs.run_gate(
            FACE,
            "contract-sync",
            ["node", SYNC_SCRIPT],
            cwd=client,
            env=ws.env(),
            repo="xadmin-client",
            timeout=120.0,
            allowed=ws.is_allowed("xadmin-server"),
            success_note=f"{SYNC_SCRIPT} 通过",
        )
    )
    results.append(
        procs.run_gate(
            FACE,
            "contract-usage",
            ["node", USAGE_SCRIPT],
            cwd=client,
            env=ws.env(),
            repo="xadmin-client",
            timeout=120.0,
            success_note=f"{USAGE_SCRIPT} 通过",
        )
    )
    return results


def _server_source(server) -> report.CheckResult:
    """服务端契约真源完整性：5 份 schema 必须齐备。"""
    source_dir = server / SOURCE_DIR
    absent = [name for name in SCHEMA_NAMES if not (source_dir / f"{name}.schema.json").is_file()]
    if absent:
        return report.CheckResult(
            FACE, "server-source", report.STATUS_FAIL, findings=[f"缺少契约源：{absent}"], repo="xadmin-server"
        )
    return report.CheckResult(
        FACE,
        "server-source",
        report.STATUS_PASS,
        evidence=[f"{SOURCE_DIR}/: {len(SCHEMA_NAMES)} 份 schema 齐备（真源）"],
        repo="xadmin-server",
    )


def _mirror_compare(ws, server, client) -> report.CheckResult:
    source_dir = server / SOURCE_DIR
    mirror_dir = client / MIRROR_DIR
    if not source_dir.is_dir():
        return report.CheckResult(
            FACE,
            "mirror-normalized",
            report.STATUS_FAIL,
            findings=[f"未找到服务端契约源 {source_dir}"],
            repo="xadmin-server",
        )
    drifted = []
    for name in SCHEMA_NAMES:
        source = source_dir / f"{name}.schema.json"
        mirror = mirror_dir / f"{name}.schema.json"
        if not source.is_file():
            drifted.append(f"{name}: 服务端契约源缺失")
            continue
        if not mirror.is_file():
            drifted.append(f"{name}: 镜像文件缺失（{MIRROR_DIR}）")
            continue
        try:
            if _normalize(source) != _normalize(mirror):
                drifted.append(f"{name}: 镜像与服务端语义不一致")
        except json.JSONDecodeError as exc:
            drifted.append(f"{name}: JSON 解析失败 {exc}")
    if drifted:
        return report.CheckResult(
            FACE,
            "mirror-normalized",
            report.STATUS_FAIL,
            findings=drifted + [f"修复：在 {MIRROR_DIR} 同步镜像（服务端为真源）"],
            evidence=[f"{SOURCE_DIR}/ ↔ {MIRROR_DIR}/"],
            repo="xadmin-client",
        )
    return report.CheckResult(
        FACE,
        "mirror-normalized",
        report.STATUS_PASS,
        evidence=[f"{len(SCHEMA_NAMES)} 份 schema 语义一致（{SOURCE_DIR}/ ↔ {MIRROR_DIR}/）"],
        repo="xadmin-client",
    )
