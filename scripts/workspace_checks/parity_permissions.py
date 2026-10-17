# -*- coding: utf-8 -*-
"""权限种子 ↔ 前端消费点对账。

真源：各层菜单种子（平台层 ``loadjson/`` + 各应用 ``<app>/seeds/``）；
对账脚本：``xadmin-client/scripts/check-menu-permissions.mjs``（种子 ↔ 前端双向对账）；
另一侧断言：menumeta 下发的菜单 i18n key 必须在前端语言包内（否则英文界面显示 key 原文）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import procs, report, textutil

FACE = "permissions"
REPOS = ("xadmin-server", "xadmin-client")

MENU_SCRIPT = "scripts/check-menu-permissions.mjs"
MENU_SEED = "loadjson/menu.json"
META_SEED = "loadjson/menumeta.json"
_TITLE_KEY_RE = re.compile(r"^[A-Za-z][\w]*\.[\w.]+$")
_LOCALES = ("locales/zh-CN.yaml", "locales/en.yaml")


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

    results.append(
        procs.run_gate(
            FACE,
            "seed-vs-frontend",
            ["node", MENU_SCRIPT],
            cwd=client,
            env=ws.env(),
            repo="xadmin-client",
            timeout=180.0,
            allowed=ws.is_allowed("xadmin-server"),
            success_note=f"{MENU_SCRIPT} 双向对账通过",
        )
    )
    results.append(_menumeta_locales(ws, server, client))
    return results


def _seed_rows(server: Path, filename: str) -> list:
    """聚合各层种子行：平台层 ``loadjson/`` + 各应用 ``<app>/seeds/``。"""

    roots = [server / "loadjson"]
    roots.extend(
        item / "seeds"
        for item in sorted(server.iterdir())
        if item.is_dir() and (item / "seeds" / "manifest.json").is_file()
    )
    rows: list = []
    for root in roots:
        path = root / filename
        if path.is_file():
            rows.extend(json.loads(path.read_text(encoding="utf-8")))
    return rows


def _menumeta_locales(ws, server: Path, client: Path) -> report.CheckResult:
    """menumeta 下发的菜单 i18n key 必须存在于前端 zh/en 语言包。"""
    try:
        menus = _seed_rows(server, "menu.json")
        metas = {item["pk"]: item.get("fields", {}) for item in _seed_rows(server, "menumeta.json")}
        if not menus or not metas:
            raise KeyError("菜单种子为空")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return report.CheckResult(
            FACE, "menumeta-locales", report.STATUS_FAIL, findings=[f"解析菜单种子失败：{exc}"], repo="xadmin-server"
        )

    keys = set()
    for row in menus:
        fields = row.get("fields", {})
        title = str(metas.get(fields.get("meta"), {}).get("title") or "").strip()
        if _TITLE_KEY_RE.match(title):
            keys.add(title)
    if not keys:
        return report.CheckResult(
            FACE,
            "menumeta-locales",
            report.STATUS_FAIL,
            findings=["未从种子提取到任何菜单 i18n key（检查种子结构）"],
            repo="xadmin-server",
        )

    missing = []
    for rel in _LOCALES:
        path = client / rel
        if not path.is_file():
            missing.append(f"{rel}（文件缺失）")
            continue
        catalog = textutil.parse_yaml_keys(path.read_text(encoding="utf-8"))
        absent = sorted(key for key in keys if key not in catalog)
        if absent:
            missing.append(f"{rel} 缺词条 {absent}")
    if missing:
        return report.CheckResult(
            FACE,
            "menumeta-locales",
            report.STATUS_FAIL,
            findings=missing,
            evidence=[f"{MENU_SEED} / {META_SEED}: {len(keys)} 个 key"],
            repo="xadmin-server",
        )
    return report.CheckResult(
        FACE,
        "menumeta-locales",
        report.STATUS_PASS,
        evidence=[f"{MENU_SEED} / {META_SEED}: {len(keys)} 个菜单 key 在前端 zh/en 语言包均可解析"],
        repo="xadmin-server",
    )
