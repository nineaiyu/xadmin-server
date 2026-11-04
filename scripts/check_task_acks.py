#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Celery 任务投递可靠性门禁：``@shared_task`` 必须显式声明 acks 策略。

背景（2026-11 审查 BE-M1 的根因治理）：默认 acks 早退（``task_acks_late=False``）
下，worker 崩溃 / 宿主重启 / 硬超时杀进程时消息已 ack、任务直接丢失——
「用户动作触发的一次性任务」丢消息 = 丢工作（导入/导出记录永久 RUNNING、
Webhook 投递静默停摆）。审查修复只能逐点补 ``acks_late=True``；本门禁把
「每个新任务必须显式选择投递语义」变成静态检查，下次新增任务漏声明即 CI 红。

三类合法声明（AST 扫描 ``@shared_task`` 装饰器，按函数全路径 ``模块:函数名`` 归档）：

1. ``acks_late=True`` —— 一次性投递任务（推荐默认）。丢消息代价高于重复执行，
   实现体须幂等（终态守卫 / CAS / 代际号等仓内既有范式）；
2. **周期 / 启动任务自动豁免** —— 同函数还装饰 ``@register_as_period_task`` 或
   ``@after_app_ready_start``：调度本身自愈（下个周期重跑），acks_late 反而引入
   崩溃后重复执行的窗口；
3. **显式豁免登记** —— 下方 ``EXEMPT`` 表：重投不安全（重复执行有真实副作用）
   或「丢失可容忍且可重新触发」（锁 / 状态机兜底）的按需任务，必须写明理由。

用法::

    python scripts/check_task_acks.py    # 发现未声明任务即 exit 1
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 扫描面与 check_cache_keys 同口径（内核包 + 全部业务 app）；
# tests/ / migrations/ / examples 不在运行面内。
SCAN_DIRS = (
    "packages/xadmin-common/common",
    "system",
    "settings",
    "captcha",
    "ai",
    "dataset",
    "message",
    "identity",
    "file",
    "task",
    "audit",
    "approval",
    "notifications",
    "devtools",
    "mfa",
    "server",
    "integrations",
    "demo",
)
IGNORE_PARTS = ("/tests/", "/migrations/")

#: 自动豁免的伴随装饰器：周期任务 / 启动任务（调度自愈，重投反而引入重复执行）
SELF_HEALING_DECORATORS = frozenset({"register_as_period_task", "after_app_ready_start"})

#: 显式豁免登记（模块:函数名 → 理由）。新增豁免必须注明理由；只增不减须评审。
EXEMPT: dict[str, str] = {
    # 重投不安全：重复执行有真实副作用
    "common.tasks:background_task_view_set_job": (
        "分片执行真实写动作（批量导入/删除）：重投=重复写；丢分片由 24h TTL 与失败行兜底（代码内注释）"
    ),
    "notifications.notifications:publish_task": "通知扇出重投=重复站内信（非幂等）：通知属旁路能力，丢失可容忍",
    "common.utils.verify_code:send_sms_async": "短信重投=重复下发（供应商计费）：验证码时效短，用户可重新发码",
    "dataset.analysis_tasks:run_scheduled_report": (
        "报表由调度扫描派发、下期自愈（report_due 按 last_run_at 判定）：丢消息代价有限"
    ),
    # 丢失可容忍且可重新触发：锁 / 状态机兜底
    "system.tasks:convert_office_preview_task": (
        "预览转换由请求侧按需重投（转换锁 TTL 自愈）：崩溃丢失由下一次预览请求重触发"
    ),
    "identity.tasks:scan_account_risks_task": "只读巡检：用户可重新触发，结果按 task_id 覆盖写缓存",
    "ai.tasks:build_embeddings_task": "单飞锁 + 状态机兜底（锁 TTL 保证入口不卡死），可由用户重新触发",
    "ai.tasks:sync_repo_task": "锁 + 状态机 + 同步状态通道（锁 TTL 兜底），可由用户重新触发",
    "ai.tasks:ingest_upload_document_task": "摄取状态机（ingest_status）+ 进程锁：失败保留可读原因，重新上传可触发",
    "ai.tasks:test_ai_profile_task": "只读连通性探测：前端凭 task_id 轮询，用户可重新发起",
    "ai.tasks:probe_ai_profile_task": "只读能力探测：同 test_ai_profile_task",
    "captcha.tasks:flush_captcha_logs": (
        "遥测旁路：设计口径明确失败不重试（防观测面故障反向拖挂验证主链路），丢事件可容忍"
    ),
    "dataset.analysis_tasks:schedule_report_run": (
        "手动运行入口：预创建 ExportRecord 后即派发，实际执行由 run_scheduled_report 承载"
    ),
    "notifications.tasks:push_messages_job": "WS 即时提醒（站内信已持久化）：丢失可容忍，重投=重复提醒",
    "demo.tasks:auto_off_shelf_books": "官方示例 app 的教学任务（任务中心手动运行示例），非生产路径",
}


def _decorator_name(node: ast.AST) -> str:
    """装饰器表达式的简名（``shared_task`` / ``register_as_period_task`` / …）。"""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _declares_acks_late(decorator: ast.AST) -> bool:
    """``@shared_task(..., acks_late=True, ...)`` 才算显式声明（False/未写不算）。"""
    if not isinstance(decorator, ast.Call):
        return False
    for keyword in decorator.keywords:
        if keyword.arg == "acks_late" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
            return True
    return False


def _module_path(py_file: Path) -> str:
    """文件路径 → 点分模块路径（与 celery 任务名 ``__module__`` 同口径）。

    内核包源码在 ``packages/xadmin-common/`` 下，但 workspace 成员以导入名
    ``common`` 安装——路径段必须剥离容器目录，否则任务键与运行时任务名对不上。
    """
    parts = list(py_file.relative_to(ROOT).with_suffix("").parts)
    if parts[:2] == ["packages", "xadmin-common"]:
        parts = parts[2:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_task_declarations(root: Path | None = None) -> list[dict[str, str]]:
    """扫描全部 ``@shared_task`` 声明，返回逐任务的政策归档记录。

    记录字段：``task``（模块:函数名）、``file``、``status``（ok / exempt / violation）、
    ``policy``（acks_late / periodic / startup / exempt / missing）。
    """
    base = root or ROOT
    records: list[dict[str, str]] = []
    for scan_dir in SCAN_DIRS:
        for py_file in sorted((base / scan_dir).rglob("*.py")):
            posix = py_file.as_posix()
            if any(part in posix for part in IGNORE_PARTS):
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError:
                continue
            module = _module_path(py_file)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = [_decorator_name(item) for item in node.decorator_list]
                if "shared_task" not in names:
                    continue
                task_id = f"{module}:{node.name}"
                if _declares_acks_late(node.decorator_list[names.index("shared_task")]):
                    status, policy = "ok", "acks_late"
                elif SELF_HEALING_DECORATORS & set(names):
                    status, policy = "ok", "periodic" if "register_as_period_task" in names else "startup"
                elif task_id in EXEMPT:
                    status, policy = "exempt", "exempt"
                else:
                    status, policy = "violation", "missing"
                records.append(
                    {"task": task_id, "file": str(py_file.relative_to(base)), "status": status, "policy": policy}
                )
    return records


def main() -> int:
    records = scan_task_declarations()
    violations = [item for item in records if item["status"] == "violation"]
    durable = sum(1 for item in records if item["policy"] == "acks_late")
    periodic = sum(1 for item in records if item["policy"] in ("periodic", "startup"))
    exempt = sum(1 for item in records if item["policy"] == "exempt")
    print(f"[task-acks] shared_task 共 {len(records)} 个：acks_late={durable} / 周期·启动={periodic} / 豁免={exempt}")
    if violations:
        print(f"[task-acks] 以下 {len(violations)} 个任务未声明投递策略（worker 崩溃即丢消息）：")
        for item in violations:
            print(f"  ✗ {item['task']}  ({item['file']})")
            print("    → 一次性任务补 acks_late=True（实现体须幂等）；周期/启动任务补 @register_as_period_task")
            print("      或 @after_app_ready_start；其余在本脚本 EXEMPT 表登记理由。")
        return 1
    print("[task-acks] 全部任务已显式声明投递策略（acks_late / 周期·启动自愈 / 豁免登记）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
