#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 工具面巡检：路由面 × AI 动作声明面比对，输出「未注册候选清单」。

工具目录维护从「记忆驱动」变「巡检驱动」：新增业务端点后跑一次即可知道哪些
端点可 AI 化而尚未登记（以及哪些声明已经失效）。

    python manage.py ai_tool_audit                 # 只报告
    python manage.py ai_tool_audit --fail-on-gap   # 存在未确认缺口时退出码 1（可入 CI）
    python manage.py ai_tool_audit --json          # 机器可读输出

口径：

- 路由面 = ``build_route_index()``（与权限点巡检同源：仅 api/ 前缀、需鉴权、跳过
  PUT/HEAD）；
- 声明面 = ``API_ACTION_SPECS``（唯一白名单注册表，MCP / 助手页 / function calling 同源）；
- 豁免清单 = ``EXEMPT_PREFIXES``（AI 自身端点 / 认证 / 文档 / 实时通道等明确不打算
  AI 化的面），与权限点 ``EXCLUDE_PERMISSION_NAMES`` 同口径；
- 未注册 ≠ 缺陷：本命令默认只报告（避免强迫注册无意义动作），``--fail-on-gap``
  用于收敛后的白名单固化。
"""

from django.core.management.base import BaseCommand

#: 明确不 AI 化的路由前缀（AI 自身 / 认证 / 文档 / 实时通道 / 内部观测）
EXEMPT_PREFIXES = (
    "api/system/ai/",
    "api/system/auth/",
    "api/system/login",
    "api/system/logout",
    "api/system/token",
    "api/system/captcha",
    "api/system/setting",
    "api/system/config",
    "api/system/menu",
    "api/system/permission",
    "api/system/field",
    "api/system/modellabelfield",
    "api/system/online",
    "api/system/monitor",
    "api/system/csp-report",
    "api/system/tags",
    "api-docs",
    "api/system/health",
)
READ_METHODS = ("GET", "HEAD")
DANGEROUS_METHODS = ("DELETE",)


def _domain(url: str) -> str:
    """URL → 分组域名（api/ 之后的第一段；system 类再细到第二段）。"""
    parts = [part for part in str(url or "").strip("/").split("/") if part]
    if len(parts) > 1 and parts[0] in ("system", "notifications", "message"):
        return "/".join(parts[:2])
    return parts[0] if parts else "?"


def _classify(method: str) -> str:
    method = method.upper()
    if method in READ_METHODS:
        return "read"
    if method in DANGEROUS_METHODS:
        return "danger"
    return "write"


class Command(BaseCommand):
    help = "AI 工具面巡检：输出可 AI 化但未注册的端点候选 + 失效声明（默认只报告）"

    def add_arguments(self, parser):
        parser.add_argument("--fail-on-gap", action="store_true", help="存在未豁免缺口时退出码 1")
        parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
        parser.add_argument("--limit", type=int, default=200, help="明细输出上限（默认 200）")
        parser.add_argument("--show-exempted", action="store_true", help="同时列出豁免端点")

    def handle(self, *args, **options):
        import json
        import re

        from ai.utils.ai_api_registry import API_ACTION_SPECS
        from ai.utils.ai_tool_triage import triage_for
        from common.swagger.ai_meta import normalize_path
        from system.utils.permission_sync import build_route_index

        routes = [route for route in build_route_index() if route.requires_permission]
        declared = {}
        for spec in API_ACTION_SPECS.values():
            declared[(str(spec.method).upper(), normalize_path(spec.path))] = spec

        candidates, exempted, covered_declared = [], [], set()
        for route in routes:
            regex = route.url if route.url.endswith("$") else f"{route.url}$"
            for method, action in route.actions.items():
                upper = method.upper()
                if upper in ("PUT", "HEAD"):
                    continue
                hit = None
                for decl_method, decl_path in declared:
                    if decl_method != upper:
                        continue
                    sample = decl_path.lstrip("/").replace("<pk>", "1")
                    if re.match(regex, sample):
                        hit = (decl_method, decl_path)
                        break
                url = route.url.rstrip("$")
                entry = {
                    "method": upper,
                    "url": url,
                    "view": route.view,
                    "action": action,
                    "kind": _classify(upper),
                    "domain": _domain(url),
                }
                if hit:
                    covered_declared.add(hit)
                    continue
                if url.startswith(EXEMPT_PREFIXES):
                    exempted.append({**entry, "reason": "基础设施前缀豁免（认证 / 文档 / 观测等）"})
                    continue
                decision = triage_for(url)
                if decision and decision[0] == "exempt":
                    exempted.append({**entry, "reason": decision[1]})
                    continue
                # register（登记为待注册）与未登记（新资源域）都计入缺口：
                # 前者推动补声明，后者强制新模块出生时做一次「AI 化 or 不 AI 化」决策
                entry["triage"] = "register" if decision else "unregistered"
                candidates.append(entry)

        stale = [
            {"method": method, "path": path, "action": spec.key}
            for (method, path), spec in declared.items()
            if (method, path) not in covered_declared
        ]
        candidates.sort(key=lambda row: (row["domain"], row["url"], row["method"]))

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "routes": len(routes),
                        "declared": len(declared),
                        "candidates": candidates,
                        "stale": stale,
                        "exempted": exempted if options["show_exempted"] else len(exempted),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            self._report(candidates, stale, exempted, options)

        if options["fail_on_gap"] and candidates:
            self.stderr.write(self.style.ERROR(f"存在 {len(candidates)} 个未注册候选（--fail-on-gap）"))
            raise SystemExit(1)

    def _report(self, candidates, stale, exempted, options):
        limit = max(1, int(options["limit"]))
        by_domain = {}
        for row in candidates:
            by_domain.setdefault(row["domain"], []).append(row)
        self.stdout.write(
            f"[AI 工具面] 未注册候选 {len(candidates)} 个 / 失效声明 {len(stale)} 个"
            f" / 豁免 {len(exempted)} 个（--show-exempted 可展开）"
        )
        for domain, rows in sorted(by_domain.items()):
            self.stdout.write(f"  [{domain}] {len(rows)} 个")
            for row in rows[:limit]:
                self.stdout.write(f"    {row['method']:6s} {row['url']}  ({row['kind']}, {row['view']})")
            if len(rows) > limit:
                self.stdout.write(f"    ... 其余 {len(rows) - limit} 个略")
        for row in stale:
            self.stdout.write(
                self.style.WARNING(f"  ~ 失效声明：{row['method']} {row['path']}（{row['action']}）未匹配到路由")
            )
        if options["show_exempted"]:
            for row in exempted:
                self.stdout.write(f"    (豁免) {row['method']:6s} {row['url']}")
