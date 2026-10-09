# -*- coding: utf-8 -*-
"""国际化词条一致性：前端 zh/en 对称 + 前端引用命中；服务端 po 仅作信息性比对。

前端真源：``xadmin-client/locales/zh-CN.yaml`` + ``en.yaml``，两侧 key 集合必须完全一致；
并跑 ``scripts/check-i18n-keys.mjs`` 覆盖「src 静态引用的 key 双侧存在」。
服务端 po（``locale/zh|en/LC_MESSAGES/django.po``）与前端 yaml 语义不同，**只记录不判失败**。
"""

from __future__ import annotations

import re

from . import procs, report, textutil

FACE = "locale"
REPOS = ("xadmin-client", "xadmin-server")

ZH = "locales/zh-CN.yaml"
EN = "locales/en.yaml"
I18N_SCRIPT = "scripts/check-i18n-keys.mjs"
PO_FILES = ("locale/zh/LC_MESSAGES/django.po", "locale/en/LC_MESSAGES/django.po")
_MSGID_RE = re.compile(r'^msgid "(.*)"', re.M)


def run(ws, tier: str = "fast") -> list:
    results: list = []
    client = ws.path("xadmin-client")
    server = ws.path("xadmin-server")

    if client is None:
        results.append(report.repo_gap(FACE, "xadmin-client", ws.is_allowed("xadmin-client")))
    else:
        results.append(_yaml_symmetry(ws, client))
        results.append(
            procs.run_gate(
                FACE,
                "i18n-keys",
                ["node", I18N_SCRIPT],
                cwd=client,
                env=ws.env(),
                repo="xadmin-client",
                timeout=120.0,
                success_note=f"{I18N_SCRIPT} 通过",
            )
        )

    if server is None:
        results.append(report.repo_gap(FACE, "xadmin-server", ws.is_allowed("xadmin-server")))
    else:
        results.append(_po_summary(server))
    return results


def _yaml_symmetry(ws, client) -> report.CheckResult:
    zh_path = client / ZH
    en_path = client / EN
    for path in (zh_path, en_path):
        if not path.is_file():
            return report.CheckResult(
                FACE, "yaml-symmetry", report.STATUS_FAIL, findings=[f"未找到语言包 {path}"], repo="xadmin-client"
            )
    zh_keys = textutil.parse_yaml_keys(zh_path.read_text(encoding="utf-8"))
    en_keys = textutil.parse_yaml_keys(en_path.read_text(encoding="utf-8"))
    only_zh = sorted(zh_keys - en_keys)
    only_en = sorted(en_keys - zh_keys)
    if only_zh or only_en:
        findings = []
        if only_zh:
            findings.append(f"仅 zh-CN.yaml 存在：{only_zh}")
        if only_en:
            findings.append(f"仅 en.yaml 存在：{only_en}")
        return report.CheckResult(
            FACE,
            "yaml-symmetry",
            report.STATUS_FAIL,
            findings=findings,
            evidence=[f"{ZH}（{len(zh_keys)} 条） / {EN}（{len(en_keys)} 条）"],
            repo="xadmin-client",
        )
    return report.CheckResult(
        FACE,
        "yaml-symmetry",
        report.STATUS_PASS,
        evidence=[f"{ZH} / {EN} key 集合完全一致（{len(zh_keys)} 条）"],
        repo="xadmin-client",
    )


def _po_summary(server) -> report.CheckResult:
    """服务端 po 信息性比对：只统计两侧 msgid 规模，不判失败（与前端 yaml 语义不同）。"""
    counts = {}
    for rel in PO_FILES:
        path = server / rel
        if path.is_file():
            msgids = [m for m in _MSGID_RE.findall(path.read_text(encoding="utf-8")) if m]
            counts[rel] = len(msgids)
    if not counts:
        return report.CheckResult(
            FACE,
            "server-po",
            report.STATUS_PASS,
            note="服务端 po 未检出（信息性比对，不判失败）",
            repo="xadmin-server",
        )
    evidence = [f"{rel}: {count} msgid" for rel, count in counts.items()]
    return report.CheckResult(
        FACE,
        "server-po",
        report.STATUS_PASS,
        note="信息性比对：服务端 po 与前端 yaml 语义不同，不作一致性判定",
        evidence=evidence,
        repo="xadmin-server",
    )
