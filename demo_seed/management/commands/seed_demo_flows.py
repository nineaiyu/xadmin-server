#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批/脱敏/表单采集演示数据：一键生成可交互的审批闭环演示数据。

与数据分析演示数据的分工（seed_demo_users 同思路）：
- 定义类数据（脱敏规则、流程定义/节点/版本、表单定义、表单提交）随
  ``load_init_json`` 作为内置示例自动灌入（loadjson/*.json，creator 一律 1）；
- 实例类数据（流程实例/节点任务、轻量审批单）必须满足「申请人 ≠ 审批人」
  的引擎语义，种子造不出合规数据，由本命令用真实引擎（create_instance /
  approve_task / reject_task）动态生成，同时保证状态机/时间戳/轨迹自洽。

命令会额外创建 2 个演示用户（demo_flow_lily 申请人 / demo_flow_chen 审批人），
并把内置流程节点的审批人改写为演示用户 + 落新版本快照，保证改完后在「流程审批」
页继续发起申请也能走通。

生成实现（常量与 DemoDataMixin）见同目录 _seed_demo_flows_data.py。

用法：

    python manage.py seed_demo_flows             # 幂等生成（固定 pk，可重复执行）
    python manage.py seed_demo_flows --reset     # 先删除演示数据与演示用户再生成
"""

from typing import Any

from django.core.management.base import BaseCommand

from approval.models import ApprovalInstance, ApprovalRequest
from demo_seed.management.commands._seed_demo_flows_data import (
    ALL_DEMO_PKS,
    DEMO_APPLIER,
    DEMO_APPROVER,
    DEMO_ASSIGNEE_VALUE,
    DEMO_PASSWORD,
    DEMO_STAFF,
    FLOW_CODES,
    INSTANCE_PKS,
    REQUEST_PKS,
    SUBMISSION_PKS,
    DemoDataMixin,
)

__all__ = [
    "ALL_DEMO_PKS",
    "DEMO_APPLIER",
    "DEMO_APPROVER",
    "DEMO_ASSIGNEE_VALUE",
    "DEMO_PASSWORD",
    "DEMO_STAFF",
    "FLOW_CODES",
    "INSTANCE_PKS",
    "REQUEST_PKS",
    "SUBMISSION_PKS",
    "Command",
]


class Command(DemoDataMixin, BaseCommand):
    help = "生成审批中心/流程审批/表单采集演示数据（demo_flow_ 前缀用户）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--reset", action="store_true", help="先删除演示数据与演示用户再生成")
        parser.add_argument("--clean-only", action="store_true", help="只清理，不生成（seed_demo_clean 编排调用）")

    # ---------------------------------------------------------------- 清理

    def _reset(self) -> None:
        from dataset.models import DynamicFormSubmission

        # 固定 pk 段 + 演示标题兜底（覆盖历史上非幂等版本/中断运行留下的随机 pk 残留）
        demo_titles = ApprovalInstance.objects.filter(title__endswith="（演示）").values_list("pk", flat=True)
        deleted, _ = ApprovalInstance.objects.filter(pk__in=[*INSTANCE_PKS, *demo_titles]).delete()
        self.stdout.write(f"removed demo instances (tasks cascade): {deleted}")
        deleted, _ = ApprovalRequest.objects.filter(pk__in=REQUEST_PKS).delete()
        self.stdout.write(f"removed demo approval requests: {deleted}")
        deleted, _ = DynamicFormSubmission.objects.filter(pk__in=SUBMISSION_PKS).delete()
        self.stdout.write(f"removed demo form submissions: {deleted}")
        # 演示用户不删（软删除也占 DB 唯一约束，重建会撞 username 冲突）：
        # _ensure_user 对软删用户做复活复用

    # ---------------------------------------------------------------- 入口

    def handle(self, *args: Any, **options: Any) -> None:
        if options["reset"] or options.get("clean_only"):
            self._reset()
        if options.get("clean_only"):
            self.stdout.write("seed_demo_flows clean-only done")
            return

        applier = self._ensure_user(DEMO_APPLIER, "演示申请人-李莉")
        approver = self._ensure_user(DEMO_APPROVER, "演示审批人-陈工")

        self._rebind_assignees()
        self._create_demo_instances(applier, approver)
        self._create_demo_requests(applier, approver)
        self._create_demo_submissions(applier)
        self.stdout.write(self.style.SUCCESS("seed_demo_flows done"))
