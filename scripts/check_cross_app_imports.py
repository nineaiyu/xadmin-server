#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""跨 app 横向 import 静态门禁（含框架层方向治理）。

扫描所有 app 内模块级（顶格）对其他 app 的 models / serializers / views /
notifications / backends / signal(s) 直接 import——这是契约层收口的坏味道。
跨 app 引用一律走 `<app>.services` 契约层；确实无法立即收口的存量，
显式登记在 ALLOWLIST 并注明原因，禁止无台账新增。

框架层方向规则：common 消费业务 app 只允许经 `<app>.services`（各 app 的
契约门面），且每条缝在 CONTRACT_SEAMS 登记原因（双向漂移校验）；
函数级惰性 import 属逃生门，作为观察项打印。

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
    "system/management/commands/dump_init_json.py": "管理命令（合法保留）",
    "system/management/commands/load_init_json.py": "管理命令（合法保留）",
    # 演示数据种子命令：与 load_init_json 同口径的管理命令合法保留（跨 app
    # 模块级 import 仅存在于命令入口，非运行期业务链路）
    "system/management/commands/seed_demo_content.py": "管理命令（合法保留）",
    "system/management/commands/seed_demo_org.py": "管理命令（合法保留）",
}

# ---------------------------------------------------------------------------
# 方向规则（框架层依赖治理）：common 是框架层，消费业务 app 只允许经
# `<app>.services` 对外契约层（各 app services.py 即官方契约门面），且每条
# 契约缝必须在此登记（含原因）——把框架层耦合从「隐形」变成「显式、可审计、
# 有上限」。登记双向校验：出现未登记的缝、或登记的缝已不存在，均为违例。
# 函数级（缩进）业务 import 仍属官方逃生门，不阻断，作为观察项打印。
# ---------------------------------------------------------------------------
FRAMEWORK_APP = "common"

CONTRACT_SEAMS = {
    "common/notifications.py": {
        "notifications.services": "通知渠道注册与系统消息发送（common 是渠道的通用生产者）",
        "system.services": "告警收件人解析（get_active_superuser_queryset）",
    },
    "common/ops_alert.py": {
        "notifications.services": "运维告警经系统消息渠道投递",
        "system.services": "告警收件人解析 + 出站 Webhook 事件投递",
    },
    "common/backup_alert.py": {
        "notifications.services": "备份结果告警经系统消息渠道投递",
        "system.services": "告警收件人解析 + 出站 Webhook 事件投递",
    },
    "common/celery/failure_handler.py": {
        "notifications.services": "任务失败告警经系统消息渠道投递",
        "system.services": "告警收件人解析（get_active_superuser_queryset）",
    },
    "common/core/config/base.py": {
        "system.services": "运行期配置模型 SystemConfig（settings 加载期即消费，结构性依赖）",
    },
    "common/core/config/user_conf.py": {
        "system.services": "用户个人配置模型 UserPersonalConfig",
    },
    "common/core/data_scope/constants.py": {
        "system.services": "数据权限模式常量（ModelLabelField / ModeTypeAbstract）",
    },
    "common/core/data_scope/values.py": {
        "system.services": "数据权限主体模型（UserInfo / DeptInfo 行级过滤）",
    },
    "common/core/filter.py": {
        "system.services": "数据行权限过滤面 + 应用凭证行级授权（apply_grant_row_scope）",
    },
    "common/core/middleware.py": {
        "system.services": "审计日志模型 OperationLog + PAT 类型判定 + 敏感操作告警分流",
    },
    "common/core/permission.py": {
        "system.services": "菜单/字段权限模型 + 应用凭证动作级授权（api_grant 三函数）",
    },
    "common/core/auth.py": {
        "system.services": "API 配额告警发布（系统消息 + 出站 Webhook）",
    },
    "common/core/approval.py": {
        "approval.services": "审批流拦截入口（process_approval 装饰器消费，3.1 拆分批次2 起 approval 自持契约门面）",
    },
    "common/core/credentials.py": {
        "system.services": "凭据巡检消费 SystemConfig（属性访问式引用，保留迁移期降级）",
    },
    "common/core/serializers.py": {
        "system.services": "应用凭证字段授权 + 字段掩码应用/规则/明文访问审计",
    },
    "common/core/modelset/base.py": {
        "system.services": "删除影响面确认校验（ensure_impact_confirmed）",
    },
    "common/core/modelset/batch.py": {
        "system.services": "批量删除影响面确认校验（ensure_impact_confirmed）",
    },
    "common/core/modelset/impact.py": {
        "system.services": "影响面预览（impact_for_many / guarded_models）",
    },
    "common/core/modules/gate.py": {
        "system.services": "模块裁剪消费 Menu（属性访问式引用，保留迁移期降级）",
    },
    "common/management/commands/_generate_crud/analysis.py": {
        "system.services": "生成器回填种子关联（Menu / UserRole / sync_model_field）",
    },
    "common/management/commands/_generate_crud/renderers.py": {
        "system.services": "生成器消费 ModelLabelField（模型节点 pk 解析）",
    },
    "common/management/commands/services/hands.py": {
        "settings.services": "启动自检消费 Setting（迁移就绪重试探测）",
        "system.services": "启动自检权限点缺口扫描（scan_permission_gaps）",
    },
    "common/swagger/ai_meta.py": {
        "ai.services": "AI 动作声明注册表（API_ACTION_SPECS，OpenAPI 元数据派生，3.1 拆分批次3 起 ai 自持契约门面）",
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
    common_dir = REPO_ROOT / FRAMEWORK_APP
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
        # 双向漂移：台账里登记的缝在文件中已不存在（缝隙移入函数级也需清理台账重登）
        for module_path in CONTRACT_SEAMS.get(rel, {}):
            if module_path not in found_seams:
                violations.append((rel, 0, f"登记的契约缝 {module_path} 已不存在——请清理 CONTRACT_SEAMS 台账"))
    return violations, observations


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
    if violations:
        print("发现未收口的跨 app 直接 import：")
        for rel, line, stmt in sorted(violations):
            print(f"  {rel}:{line}  {stmt}")
        print(
            "\n跨 app 引用请改走 <app>.services 契约层；确需保留的，"
            "在 scripts/check_cross_app_imports.py 的 ALLOWLIST 登记原因。\n"
            "common（框架层）→ 业务 app 的契约缝请在 CONTRACT_SEAMS 登记并注明原因。"
        )
        return 1
    if observations:
        print("观察项（common 内函数级业务 import，逃生门不阻断，建议定期收口）：")
        for rel, modules in sorted(observations.items()):
            print(f"  {rel}: {sorted(modules)}")
    print(
        f"跨 app import 门禁通过（allowlist {len(ALLOWLIST)} 项合法保留；"
        f"框架层契约缝 {sum(len(v) for v in CONTRACT_SEAMS.values())} 条已登记）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
