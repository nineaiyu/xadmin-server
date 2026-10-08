#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""跨 app 横向 import 静态门禁（含框架层方向治理）。

扫描所有 app 内模块级（顶格）对其他 app 的 models / serializers / views /
notifications / backends / signal(s) 直接 import——这是契约层收口的坏味道。
跨 app 引用一律走 `<app>.services` 契约层；确实无法立即收口的存量，
显式登记在 ALLOWLIST 并注明原因，禁止无台账新增。

框架层方向规则（单缝收敛）：common 消费业务 app 的唯一出口是
`packages/xadmin-common/common/contracts.py`（声明式契约面）——common 内除该文件外，任何模块级
业务 import（含 `*.services` 形态）一律违例；contracts.py 自身仍受
「仅 `*.services`」+ CONTRACT_SEAMS 登记约束（双向漂移校验）。
函数级惰性 import 属逃生门，作为观察项打印。

反向依赖规则：common（框架层）禁止 import server（工程层）——
common 是被所有人依赖的基座，反向依赖破坏分层单向性。
server 装配产物经 packages/xadmin-common/common/injection.py 依赖注入下发，无契约缝可言，
模块级与函数级 import 一律违例（不留逃生门）。

用法：python scripts/check_cross_app_imports.py
新增违例时退出码 1（CI 阻断）。
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def discover_apps() -> set[str]:
    """按目录约定自动发现一级 Django 应用（含 apps.py 的仓库根目录）。

    历史 APPS 集合硬编码——新增业务 app 时门禁对新代码静默失明，且二开者
    不得不修改本门禁脚本才能纳管自己的 app。改为 apps.py 目录约定后，
    新 app 自动纳入扫描与契约保护（三方库不在仓库根，天然不误收）。
    """
    return {p.name for p in REPO_ROOT.iterdir() if p.is_dir() and (p / "apps.py").is_file()}


APPS = discover_apps()
SCAN_DIRS = sorted(APPS) + ["ops", "server"]

# 模块级顶层 import 才算耦合（函数内惰性 import 是官方许可的逃生门）
SMELL_PATTERN = re.compile(
    r"^(?:from ([a-z_]+)\.(models|serializers|views|notifications|backends|signal_handler|signal)\b"
    r"|import ([a-z_]+)\.(models|serializers|views|notifications|backends|signal_handler|signal)\b)",
    re.M,
)

# 合法保留清单：path -> 原因
ALLOWLIST = {
    "demo/models.py": "FK 跨 app model 引用（规划允许保留）",
    # 管理命令（合法保留）：跨 app 模块级 import 仅存在于命令入口，
    # 非运行期业务链路（Phase C 四域切分后 system 命令直连 identity 等域模型）
    "system/management/commands/dump_init_json.py": "管理命令（合法保留）",
    "system/management/commands/load_init_json.py": "管理命令（合法保留）",
    "demo_seed/management/commands/seed_demo_content.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_org.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_extras.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_users.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_admin.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_book.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_clean.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/_seed_demo_flows_data.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_flows.py": "演示种子命令（合法保留）",
    "demo_seed/management/commands/seed_demo_leave.py": "演示种子命令（合法保留）",
    "system/management/commands/sync_menu_permissions.py": "管理命令（合法保留）",
    "system/management/commands/audit_data_permission_rules.py": "管理命令（合法保留）",
    "system/management/commands/classify_upload_files.py": "管理命令（合法保留）",
    "system/management/commands/doctor.py": "管理命令（合法保留）",
    "system/management/commands/post_upgrade.py": "管理命令（合法保留）",
}

# ---------------------------------------------------------------------------
# 方向规则（框架层依赖治理，单缝收敛）：common 是框架层，业务能力
# 消费唯一出口是 packages/xadmin-common/common/contracts.py（声明式契约面：
# 白名单 + Protocol + PEP 562 惰性解析）——框架内核内其余文件出现任何业务 app
# 模块级 import（含 `*.services`）即违例。contracts.py 自身只允许经
# `<app>.services` 消费，且每条缝在 CONTRACT_SEAMS 登记原因。登记双向校验：
# 出现未登记的缝、或登记的缝已不存在，均为违例。函数级（缩进）业务 import
# 仍属官方逃生门，不阻断，作为观察项打印。
# ---------------------------------------------------------------------------
# 框架内核：分发名 xadmin-common（工作区成员），源码目录相对仓库根路径
FRAMEWORK_APP = "common"
FRAMEWORK_DIR = "packages/xadmin-common/common"

CONTRACTS_MODULE = f"{FRAMEWORK_DIR}/contracts.py"

# contracts.py 的缝以 _CONTRACT_PROVIDERS 白名单声明（PEP 562 惰性解析，文件无
# 模块级业务 import），漂移校验对照白名单的提供方声明而非 import 语句：
# 条目形如 `"Name": ("app.services", "原因"),`，捕获提供方模块
CONTRACT_PROVIDER_RE = re.compile(r'^\s*"[A-Za-z_]\w*": \("([a-z_]+\.[a-z_]+)",', re.M)

CONTRACT_SEAMS = {
    CONTRACTS_MODULE: {
        "notifications.services": "框架层业务消费唯一显式契约出口：消息渠道生产面（5 名字）",
        "identity.services": "框架层业务消费唯一显式契约出口：身份域模型与应用凭证委托（Phase C 四域切分）",
        "audit.services": "框架层业务消费唯一显式契约出口：审计域模型与掩码/影响面委托（Phase C 四域切分）",
        "task.services": "框架层业务消费唯一显式契约出口：任务域 Webhook 事件投递（Phase C 四域切分）",
        "system.services": "框架层业务消费唯一显式契约出口：platform 域模型契约 + 审计/任务委托过渡缝",
        "approval.services": "框架层业务消费唯一显式契约出口：审批流拦截入口",
        "ai.services": "框架层业务消费唯一显式契约出口：AI 动作声明注册表",
        "settings.services": "框架层业务消费唯一显式契约出口：Setting 启动自检 + 文档站登录锁定",
    },
}

# common 内模块级业务 import（含 services 契约层）：from <app>[.sub] import / import <app>[.sub]
# 与 SEAM_LAZY_PATTERN 保持同构（4 个组），共用 _seam_module_from_match 还原
SEAM_IMPORT_PATTERN = re.compile(
    r"^(?:from ([a-z_]+)(\.[a-z_.]+)? import|import ([a-z_]+)(\.[a-z_.]+)?)",
    re.M,
)
# 观察项：common 内函数级（缩进）业务 import（不阻断，仅可见性）。
# 锚定用 [ \t] 而非 \s：\s 会跨行吞掉空行，把空行后的模块级 import 误报为
# 函数级（历史缺陷：failure_handler / data_scope 等文件的模块级 import 被
# 误报为观察项，2026-09-26 观察项收口时发现并修正）
SEAM_LAZY_PATTERN = re.compile(
    r"^[ \t]+(?:from ([a-z_]+)(\.[a-z_.]+)? import|import ([a-z_]+)(\.[a-z_.]+)?)",
    re.M,
)


def relative_module(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _seam_module_from_match(m) -> tuple[str, str] | None:
    """从 SEAM_IMPORT_PATTERN 命中还原 (业务 app, 导入模块路径)。非业务 app 返回 None。"""
    app = m.group(1) or m.group(3)
    sub = (m.group(2) or m.group(4) or "").lstrip(".")
    if app == FRAMEWORK_APP or app not in APPS:
        return None
    return app, f"{app}.{sub}" if sub else app


def scan_framework_direction():
    """common（框架层）→ 业务 app 的方向治理：返回 (违例, 观察项)。"""
    violations = []
    observations = {}
    common_dir = REPO_ROOT / FRAMEWORK_DIR
    for py in sorted(common_dir.rglob("*.py")):
        if {"migrations", "tests", "__pycache__"} & set(py.parts):
            continue
        rel = relative_module(py)
        text = py.read_text(encoding="utf-8", errors="ignore")
        found_seams: set[str] = set()
        for m in SEAM_IMPORT_PATTERN.finditer(text):
            parsed = _seam_module_from_match(m)
            if parsed is None:
                continue
            app, module_path = parsed
            line = text[: m.start()].count("\n") + 1
            if rel != CONTRACTS_MODULE:
                violations.append(
                    (rel, line, f"框架层业务消费须统一经 {CONTRACTS_MODULE}，禁止直接 import {module_path}")
                )
                continue
            if not module_path.startswith(f"{app}.services"):
                violations.append(
                    (rel, line, f"框架层须经 <app>.services 契约层消费业务 app，禁止直接 import {module_path}")
                )
                continue
            found_seams.add(module_path)
            registered = CONTRACT_SEAMS.get(rel, {}).get(module_path)
            if registered is None:
                violations.append((rel, line, f"未登记的契约缝 {module_path}——请在 CONTRACT_SEAMS 登记并注明原因"))
        for m in SEAM_LAZY_PATTERN.finditer(text):
            parsed = _seam_module_from_match(m)
            if parsed is None:
                continue
            _, module_path = parsed
            observations.setdefault(rel, set()).add(module_path)
        # 双向漂移：台账里登记的缝在文件中已不存在（缝隙移入函数级也需清理台账重登）。
        # contracts.py 无模块级业务 import，漂移改为「白名单提供方 ↔ 台账」双向对照
        if rel == CONTRACTS_MODULE:
            declared = set(CONTRACT_PROVIDER_RE.findall(text))
            for module_path in sorted(set(CONTRACT_SEAMS.get(rel, {})) - declared):
                violations.append((rel, 0, f"登记的契约缝 {module_path} 已不存在——请清理 CONTRACT_SEAMS 台账"))
            for module_path in sorted(declared - set(CONTRACT_SEAMS.get(rel, {}))):
                violations.append((rel, 0, f"未登记的契约缝 {module_path}——请在 CONTRACT_SEAMS 登记并注明原因"))
        else:
            for module_path in CONTRACT_SEAMS.get(rel, {}):
                if module_path not in found_seams:
                    violations.append((rel, 0, f"登记的契约缝 {module_path} 已不存在——请清理 CONTRACT_SEAMS 台账"))
    return violations, observations


# ---------------------------------------------------------------------------
# 反向依赖门禁：common（框架层）→ server（工程层）禁止 import。
# 例外从无：server 装配产物（CONFIG / VERSION）经 packages/xadmin-common/common/injection.py 依赖注入
# 下发（server/const.py 末尾登记），thread-local 请求持有器与表前缀信号已归位
# （packages/xadmin-common/common/local.py、packages/xadmin-common/common/core/db/prefix.py，server/utils.py 留兼容 re-export）。
# 函数级惰性 import 同样违例——工程层不是业务 app，不存在「运行期才可判定」的
# 循环依赖，逃生门只会让反向依赖回潮。
# ---------------------------------------------------------------------------
REVERSE_DEP_FRAMEWORK = FRAMEWORK_DIR
REVERSE_DEP_PROJECT = "server"

# 语句级锚定（含函数级缩进）：from server[.x] import / import server[.x]
REVERSE_DEP_IMPORT_RE = re.compile(
    r"^[ \t]*(?:from server(?:\.[\w.]+)?\s+import\b|import server(?:\.[\w.]+)?\b)",
    re.M,
)


def scan_common_to_server() -> list[tuple[str, int, str]]:
    """common 内任何形式的 server import（模块级/函数级）均为违例。"""
    violations = []
    common_dir = REPO_ROOT / REVERSE_DEP_FRAMEWORK
    for py in sorted(common_dir.rglob("*.py")):
        if {"migrations", "tests", "__pycache__"} & set(py.parts):
            continue
        rel = relative_module(py)
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in REVERSE_DEP_IMPORT_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            violations.append(
                (
                    rel,
                    line,
                    f"框架层禁止 import 工程层 {m.group(0).strip()}——"
                    "server 产物经 packages/xadmin-common/common/injection.py 注入或归位模块读取",
                )
            )
    return violations


def scan() -> list[tuple[str, int, str]]:
    violations = []
    for path in REPO_ROOT.iterdir():
        if path.name not in SCAN_DIRS or not path.is_dir():
            continue
        for py in path.rglob("*.py"):
            rel = relative_module(py)
            parts = rel.split("/")
            # src_app 取相对路径首段——不能用 py.parts[0]：iterdir 产出绝对路径，
            # 其首段恒为 "/"，会让本扫描整体空转（2026-09-26 批次3 修复的存量缺陷）
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
    direction_violations, observations = scan_framework_direction()
    violations.extend(direction_violations)
    violations.extend(scan_common_to_server())
    if violations:
        print("发现未收口的跨 app 直接 import：")
        for rel, line, stmt in sorted(violations):
            print(f"  {rel}:{line}  {stmt}")
        print(
            "\n跨 app 引用请改走 <app>.services 契约层；确需保留的，"
            "在 scripts/check_cross_app_imports.py 的 ALLOWLIST 登记原因。\n"
            "common（框架层）→ 业务 app 的消费统一经 packages/xadmin-common/common/contracts.py 契约面："
            "在 _CONTRACT_PROVIDERS 声明名字，并在 CONTRACT_SEAMS 登记提供方缝。\n"
            "common（框架层）→ server（工程层）禁止 import：装配产物经 packages/xadmin-common/common/injection.py "
            "依赖注入，请求持有器/表前缀信号用归位模块（common.local、common.core.db）。"
        )
        return 1
    if observations:
        print("观察项（common 内函数级业务 import，逃生门不阻断，建议定期收口）：")
        for rel, modules in sorted(observations.items()):
            print(f"  {rel}: {sorted(modules)}")
    print(
        f"跨 app import 门禁通过（allowlist {len(ALLOWLIST)} 项合法保留；"
        f"框架层契约缝 {sum(len(v) for v in CONTRACT_SEAMS.values())} 条已登记；"
        f"common → server 反向依赖 0 处）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
