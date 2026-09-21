#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""上线冒烟：AI 助手 + 审批流程全链路真实验证（容器内运行）。

与 pytest 的分工：pytest 走测试库 + APIClient（超管居多），本脚本走**运行库 +
真实 HTTP + 普通用户 JWT**，覆盖「权限点命中 / 白名单语义 / 角色授权」这类
只有真实环境才暴露的问题（历史缺陷：运行库权限点正则是旧版导致普通用户全 403，
单测全绿也发现不了）。

用法（容器内）::

    docker exec xadmin-server sh -c "cd /data/xadmin-server && python scripts/smoke_ai_approval.py"
    # 指定普通用户与流程参数
    ... python scripts/smoke_ai_approval.py --user demo_staff --approver demo_lead --admin isummer

退出码：0 = 全部通过；1 = 存在失败项。
"""

import argparse
import json
import os
import sys

import django
import requests

sys.path.insert(0, "/data/xadmin-server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django.setup()

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8896")
H = {"Accept": "application/json"}
FAILURES: list = []


def client_as(username: str) -> requests.Session:
    """以指定用户构造已认证会话（JWT：官方支持的自助客户端认证方式，绕开登录验证码）。"""
    from django.contrib.auth import get_user_model
    from rest_framework_simplejwt.tokens import RefreshToken

    user = get_user_model().objects.get(username=username)
    session = requests.Session()
    session.headers.update(H)
    session.headers["Authorization"] = f"Bearer {RefreshToken.for_user(user).access_token}"
    return session


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  | ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return ok


def smoke_ai(user: str) -> None:
    print(f"\n== AI 助手（{user}）==")
    session = client_as(user)

    resp = session.get(f"{BASE}/api/system/ai/assistant/status")
    body = resp.json()
    check(
        "status 可用",
        resp.status_code == 200 and body.get("code") == 1000,
        f"enabled={body.get('data', {}).get('enabled')}",
    )

    resp = session.get(f"{BASE}/api/system/ai/assistant/tools")
    tools = (resp.json().get("data") or {}).get("tools") or []
    check(
        "工具目录非空（权限点命中）",
        resp.status_code == 200 and len(tools) > 0,
        f"{len(tools)} 个动作",
    )

    resp = session.post(f"{BASE}/api/system/ai/assistant/ask", json={"question": "系统有哪些功能？"})
    answer = (resp.json().get("data") or {}).get("answer") or ""
    check("文档问答返回回答", resp.status_code == 200 and len(answer) > 0, f"{len(answer)} 字")

    resp = session.get(f"{BASE}/api/system/ai/assistant/history")
    check(
        "对话历史可读（status 组正则含 history）",
        resp.status_code == 200 and resp.json().get("code") == 1000,
    )


def smoke_approval(applicant: str, approver: str, admin: str) -> None:
    print(f"\n== 审批流程（{applicant} → {approver} → {admin}）==")
    staff, lead, boss = client_as(applicant), client_as(approver), client_as(admin)

    flows = staff.get(f"{BASE}/api/system/approval-instances/available-flows").json().get("data") or []
    leave = next((flow for flow in flows if flow.get("code") == "leave"), None)
    if not check("可发起流程列表含 leave", leave is not None, f"{[f.get('code') for f in flows]}"):
        return

    # days>3 触发第二节点（节点条件：days > 3 → 人事复核）
    resp = staff.post(
        f"{BASE}/api/system/approval-instances",
        json={"flow": leave["pk"], "title": "上线冒烟-请假", "form_data": {"days": 5, "reason": "脚本冒烟"}},
    )
    payload = resp.json()
    if not check("发起申请", resp.status_code == 200 and payload.get("code") == 1000, str(payload.get("detail"))):
        return
    instance_pk = payload["data"]["pk"]

    resp = lead.post(f"{BASE}/api/system/approval-instances/{instance_pk}/approve", json={"comment": "冒烟-节点1"})
    if not check("主管通过（节点1）", resp.json().get("code") == 1000, str(resp.json().get("detail"))):
        return

    resp = boss.post(f"{BASE}/api/system/approval-instances/{instance_pk}/approve", json={"comment": "冒烟-节点2"})
    if not check("人事复核通过（节点2）", resp.json().get("code") == 1000, str(resp.json().get("detail"))):
        return

    detail = boss.get(f"{BASE}/api/system/approval-instances/{instance_pk}").json().get("data") or {}
    status = detail.get("status") or {}
    check(
        "实例终态为已通过",
        str(status.get("value") if isinstance(status, dict) else status) == "APPROVED",
        json.dumps(status, ensure_ascii=False),
    )

    pending = boss.get(f"{BASE}/api/system/approval-instances/pending-count").json().get("data") or {}
    check("待办计数可读", "pending" in pending, str(pending))


def smoke_transfer(applicant: str, approver: str, admin: str) -> None:
    """转交与管理视角：审批人把待办转给管理员 → 归属转移 → 管理视角（全部在途）可见。"""
    print(f"\n== 审批转交 + 管理视角（{applicant} → {approver} → {admin}）==")
    staff, lead, boss = client_as(applicant), client_as(approver), client_as(admin)

    flows = staff.get(f"{BASE}/api/system/approval-instances/available-flows").json().get("data") or []
    leave = next((flow for flow in flows if flow.get("code") == "leave"), None)
    if not check("可发起流程列表含 leave", leave is not None):
        return

    resp = staff.post(
        f"{BASE}/api/system/approval-instances",
        json={"flow": leave["pk"], "title": "上线冒烟-转交", "form_data": {"days": 2, "reason": "脚本冒烟-转交"}},
    )
    payload = resp.json()
    if not check("发起申请（转交场景）", payload.get("code") == 1000, str(payload.get("detail"))):
        return
    instance_pk = payload["data"]["pk"]

    resp = lead.post(
        f"{BASE}/api/system/approval-instances/{instance_pk}/transfer",
        json={"username": admin, "comment": "脚本冒烟-转交"},
    )
    if not check("转交待办给管理员", resp.json().get("code") == 1000, str(resp.json().get("detail"))):
        return

    pending = boss.get(f"{BASE}/api/system/approval-instances?scope=pending").json().get("data") or {}
    pending_pks = {row.get("pk") for row in (pending.get("results") or [])}
    check("管理员待办接管该申请", str(instance_pk) in pending_pks, f"待办 {pending.get('total')} 条")

    resp = boss.get(f"{BASE}/api/system/approval-instances?scope=ongoing")
    ongoing = resp.json().get("data") or {}
    ongoing_pks = {str(row.get("pk")) for row in (ongoing.get("results") or [])}
    check(
        "管理视角（全部在途）可见",
        resp.status_code == 200 and str(instance_pk) in ongoing_pks,
        f"在途 {ongoing.get('total')} 条",
    )

    resp = boss.post(f"{BASE}/api/system/approval-instances/{instance_pk}/approve", json={"comment": "冒烟-转交后通过"})
    check("转交后由管理员通过", resp.json().get("code") == 1000, str(resp.json().get("detail")))


def smoke_export_batch_progress(applicant: str, approver: str, admin: str) -> None:
    """导出 / 批量转交 / 达标线预览（后两项依赖转交与管理视角权限点已同步）。"""
    print(f"\n== 导出 + 批量转交 + 达标线（{applicant} / {approver} / {admin}）==")
    staff, lead, boss = client_as(applicant), client_as(approver), client_as(admin)

    flows = staff.get(f"{BASE}/api/system/approval-instances/available-flows").json().get("data") or []
    leave = next((flow for flow in flows if flow.get("code") == "leave"), None)
    if not check("可发起流程列表含 leave", leave is not None):
        return

    pks = []
    for index in (1, 2):
        resp = staff.post(
            f"{BASE}/api/system/approval-instances",
            json={
                "flow": leave["pk"],
                "title": f"上线冒烟-批量{index}",
                "form_data": {"days": 2, "reason": "脚本冒烟-批量转交"},
            },
        )
        payload = resp.json()
        if payload.get("code") != 1000:
            check(f"发起申请（第 {index} 条）", False, str(payload.get("detail")))
            return
        pks.append(payload["data"]["pk"])
    check("发起 2 条申请", len(pks) == 2)

    # 导出（申请人视角：导出自己的申请；字段权限已按演示角色配置）
    resp = staff.get(f"{BASE}/api/system/approval-instances/export-data?type=csv")
    content = resp.content.decode("utf-8-sig", errors="replace")
    check(
        "导出 CSV 含申请数据",
        resp.status_code == 200 and "上线冒烟-批量" in content,
        f"HTTP {resp.status_code} ct={resp.headers.get('Content-Type')} 前 60 字={content[:60]!r}",
    )

    # 批量转交：两条一次交给管理员
    resp = lead.post(
        f"{BASE}/api/system/approval-instances/batch-transfer",
        json={"pks": pks, "username": admin, "comment": "脚本冒烟-批量转交"},
    )
    body = resp.json()
    success = (body.get("data") or {}).get("success")
    check("批量转交 2 条待办", body.get("code") == 1000 and success == 2, str(body.get("detail")))

    # 达标线预览：转交后由管理员持有待办，读详情看节点进度
    row = boss.get(f"{BASE}/api/system/approval-instances/{pks[0]}").json().get("data") or {}
    progress = row.get("node_progress") or {}
    check(
        "节点进度（达标线）可见",
        progress.get("required", 0) >= 1 and progress.get("total", 0) >= 1,
        json.dumps(progress, ensure_ascii=False),
    )

    # 收尾：管理员通过两条（避免遗留测试数据占据待办）
    for pk in pks:
        resp = boss.post(f"{BASE}/api/system/approval-instances/{pk}/approve", json={"comment": "冒烟-批量后通过"})
        if resp.json().get("code") != 1000:
            check(f"收尾通过 {str(pk)[:8]}", False, str(resp.json().get("detail")))
            return
    check("批量转交后逐条通过", True)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI + 审批全链路冒烟")
    parser.add_argument("--user", default="demo_staff", help="普通用户（AI 视角）")
    parser.add_argument("--approver", default="demo_lead", help="一级审批人")
    parser.add_argument("--admin", default="isummer", help="二级审批人 / 超管")
    args = parser.parse_args()

    smoke_ai(args.user)
    smoke_approval(args.user, args.approver, args.admin)
    smoke_transfer(args.user, args.approver, args.admin)
    smoke_export_batch_progress(args.user, args.approver, args.admin)

    print("\n== 汇总 ==")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项 -> {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
