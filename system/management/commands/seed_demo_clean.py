#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键卸载演示数据：清理 seed_demo_* 生成的全部数据，并回滚对内置种子的改写。

执行顺序（先业务数据、后组织与用户，避免外键拦截）：

1. ``seed_demo_book --clean-only``     图书上架示例（菜单/权限点/流程/二次确认开关）；
2. ``seed_demo_content --clean-only``  通知/聊天/知识库/文件/委托/Webhook/应用；
3. ``seed_demo_leave --clean-only``    演示请假单与关联流程实例；
4. ``seed_demo_flows --clean-only``    演示流程实例/轻量审批单/表单提交；
5. ``seed_demo_org --clean-only``      示例组织/角色/四层权限/场景模板；
6. ``seed_demo_users --clean-only``    批量演示用户（demo_数字 前缀，硬删）；
6. 回滚流程节点审批人改写（恢复 loadjson/approvalflownode.json 种子值）与
   演示版本快照（remark="演示审批人配置"，version 回落到现存快照）；
7. 回滚「演示部门」负责人（恢复 loadjson/deptinfo.json 种子值）；
8. 清理残余演示用户（demo_flow_* 等，硬删；被业务数据引用拦截时降级软删并提示）。

内置定义类数据（loadjson 的示例流程/表单/数据集/看板等）**不属于卸载范围**：
它们由 ``load_init_json`` 维护，属于系统内置示例而非本批演示数据。

用法：

    python manage.py seed_demo_clean            # 卸载全部演示数据
"""

import json
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import ProtectedError

from system.models import DeptInfo, UserInfo

DEMO_USER_PREFIX = "demo_"
DEMO_ASSIGNEE_MARK = "demo_flow_"
DEMO_DEPT_CODE = "demo"
DEMO_VERSION_REMARK = "演示审批人配置"
# 内置种子文件（回滚依据：与 load_init_json 同一来源，保证口径一致）
NODE_SEED_FILE = "approvalflownode.json"
FLOW_SEED_FILE = "approvalflow.json"
DEPT_SEED_FILE = "deptinfo.json"


def _load_seed(filename: str) -> list:
    path = os.path.join(settings.PROJECT_DIR, "loadjson", filename)
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


class Command(BaseCommand):
    help = "一键卸载全部演示数据（清理 seed_demo_* 生成的数据并回滚对内置种子的改写）"

    def add_arguments(self, parser):
        parser.add_argument("--keep-users", action="store_true", help="保留演示用户（默认一并清理）")

    def handle(self, *args, **options):
        # 1) 各命令自清理（顺序 = 业务数据 → 组织 → 用户，避免外键拦截）
        call_command("seed_demo_book", clean_only=True)
        call_command("seed_demo_content", clean_only=True)
        call_command("seed_demo_leave", clean_only=True)
        call_command("seed_demo_flows", clean_only=True)
        call_command("seed_demo_org", clean_only=True)
        call_command("seed_demo_users", clean_only=True)

        # 2) 回滚对内置种子的改写
        self._restore_builtin_flow_nodes()
        self._restore_demo_dept_leader()

        # 3) 残余演示用户（demo_flow_* 等；org/users 未覆盖的部分）
        if not options["keep_users"]:
            self._remove_demo_users()

        self.stdout.write(self.style.SUCCESS("seed_demo_clean done"))

    # ---------------------------------------------------------------- 回滚

    def _restore_builtin_flow_nodes(self):
        """恢复被演示命令改写的流程节点审批人与版本号（严格限定：当前值含 demo_flow_ 标记）。"""
        from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalFlowVersion

        # 种子 pk 是字符串、ORM 主键是 UUID 对象：统一按字符串比对
        seed = {str(row["pk"]): row["fields"].get("assignee_value", "") for row in _load_seed(NODE_SEED_FILE)}
        restored = 0
        for node in ApprovalFlowNode.objects.filter(pk__in=list(seed)):
            seed_value = seed.get(str(node.pk))
            if seed_value is None:
                continue
            if DEMO_ASSIGNEE_MARK in (node.assignee_value or "") and node.assignee_value != seed_value:
                node.assignee_value = seed_value
                node.save(update_fields=["assignee_value", "updated_time"])
                restored += 1
        self.stdout.write(f"restored builtin flow nodes: {restored}")

        demo_versions = ApprovalFlowVersion.objects.filter(remark=DEMO_VERSION_REMARK)
        flow_ids = list(demo_versions.values_list("flow_id", flat=True).distinct())
        removed = demo_versions.delete()[0]
        self.stdout.write(f"removed demo flow version snapshots: {removed}")
        for flow in ApprovalFlow.objects.filter(pk__in=flow_ids):
            latest = ApprovalFlowVersion.objects.filter(flow=flow).order_by("-version").first()
            target = latest.version if latest else 1
            if flow.version != target:
                flow.version = target
                flow.save(update_fields=["version", "updated_time"])

    def _restore_demo_dept_leader(self):
        """恢复「演示部门」负责人为种子值（seed_demo_leave 曾改写为演示审批人）。"""
        seed = {
            str(row["pk"]): row["fields"].get("leader")
            for row in _load_seed(DEPT_SEED_FILE)
            if row["fields"].get("code") == DEMO_DEPT_CODE
        }
        restored = 0
        for pk, seed_leader in seed.items():
            dept = DeptInfo.objects.filter(pk=pk).first()
            if dept is not None and str(dept.leader_id) != str(seed_leader):
                dept.leader_id = seed_leader
                dept.save(update_fields=["leader", "updated_time"])
                restored += 1
        self.stdout.write(f"restored demo dept leader: {restored}")

    # ---------------------------------------------------------------- 用户

    def _remove_demo_users(self):
        # 硬删优先（彻底释放 username 唯一约束）；被业务数据引用拦截（PROTECT）时降级软删
        queryset = UserInfo.all_objects.filter(username__startswith=DEMO_USER_PREFIX)
        total = queryset.count()
        if not total:
            self.stdout.write("no remaining demo users")
            return
        try:
            deleted, _rows = queryset.delete()
            self.stdout.write(f"removed demo users (hard): {deleted}")
        except ProtectedError as error:
            soft_count = UserInfo.objects.filter(username__startswith=DEMO_USER_PREFIX).count()
            UserInfo.objects.filter(username__startswith=DEMO_USER_PREFIX).delete()
            self.stdout.write(
                self.style.WARNING(f"hard delete blocked ({error}); soft-deleted {soft_count} demo users instead")
            )
