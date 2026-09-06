#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""跨 app 横向 import 静态门禁（T2.2）。

扫描所有 app 内模块级（顶格）对其他 app 的 models / serializers / views /
notifications / backends / signal(s) 直接 import——这是契约层收口的坏味道。
跨 app 引用一律走 `<app>.services` 契约层；确实无法立即收口的存量，
显式登记在 ALLOWLIST 并注明原因，禁止无台账新增。

用法：python scripts/check_cross_app_imports.py
新增违例时退出码 1（CI 阻断）。
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS = {"common", "system", "notifications", "message", "settings", "captcha", "demo", "mfa"}
SCAN_DIRS = sorted(APPS) + ["utils", "server"]

# 模块级顶层 import 才算耦合（函数内惰性 import 是官方许可的逃生门）
SMELL_PATTERN = re.compile(
    r"^(?:from ([a-z_]+)\.(models|serializers|views|notifications|backends|signal_handler|signal)\b"
    r"|import ([a-z_]+)\.(models|serializers|views|notifications|backends|signal_handler|signal)\b)",
    re.M,
)

# 合法保留清单：path -> 原因
ALLOWLIST = {
    "demo/models.py": "FK 跨 app model 引用（规划允许保留）",
    "common/management/commands/services/hands.py": "管理命令服务辅助（合法保留）",
    "system/management/commands/dump_init_json.py": "管理命令（合法保留）",
    "system/management/commands/load_init_json.py": "管理命令（合法保留）",
}


def relative_module(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def scan() -> list[tuple[str, int, str]]:
    violations = []
    for path in REPO_ROOT.iterdir():
        if path.name not in SCAN_DIRS or not path.is_dir():
            continue
        for py in path.rglob("*.py"):
            rel = relative_module(py)
            parts = py.parts
            if any(seg in {"migrations", "tests", "__pycache__", ".venv", "node_modules"} for seg in parts):
                continue
            src_app = parts[0]
            if src_app not in APPS:
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            for m in SMELL_PATTERN.finditer(text):
                target_app = m.group(1) or m.group(3)
                if target_app == src_app or target_app not in APPS:
                    continue
                if rel in ALLOWLIST:
                    continue
                line = text[: m.start()].count("\n") + 1
                violations.append((rel, line, m.group(0).strip()))
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print("发现未收口的跨 app 直接 import：")
        for rel, line, stmt in sorted(violations):
            print(f"  {rel}:{line}  {stmt}")
        print(
            "\n跨 app 引用请改走 <app>.services 契约层；确需保留的，"
            "在 scripts/check_cross_app_imports.py 的 ALLOWLIST 登记原因。"
        )
        return 1
    print(f"跨 app import 门禁通过（allowlist {len(ALLOWLIST)} 项合法保留）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
