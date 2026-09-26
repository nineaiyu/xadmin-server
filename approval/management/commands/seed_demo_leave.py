#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""请假业务演示数据：真实审批闭环，不是造状态。

与 seed_demo_flows 的分工：
- 流程定义（code=leave）随 ``load_init_json`` 作为内置示例灌入（loadjson/*.json）；
- 请假的**实例类**数据必须「申请人 ≠ 审批人」且审批轨迹自洽，种子造不出合规数据，
  由本命令用真实引擎（``submit_leave`` → ``approve_task`` / ``reject_task``）推进。

同时保证演示环境可交互：
1. 复用/创建演示用户 ``demo_flow_lily``（申请人）与 ``demo_flow_chen``（审批人），
   不可登录（unusable password）；
2. 内置「演示部门」的负责人设为 chen、lily 归属该部门——请假流程首节点是
   「直属主管审批（leader）」，没有部门负责人时提交会被引擎 fail-closed 拒绝。

用法：

    python manage.py seed_demo_leave            # 幂等生成（固定 pk，可重复执行）
    python manage.py seed_demo_leave --reset    # 先删除演示请假单与其流程实例再生成
"""

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from approval.models import ApprovalInstance, ApprovalNodeTask, Leave
from approval.utils.leave import submit_leave
from system.services import DeptInfo, UserInfo

# 固定 pk 段（与 loadjson 的 5eed 段、seed_demo_flows 的 6eed0001~0003 段区分）
LEAVE_PKS = [f"6eed0004-0000-4000-8000-00000000000{i}" for i in range(1, 5)]

DEMO_APPLIER = "demo_flow_lily"
DEMO_APPROVER = "demo_flow_chen"
DEMO_DEPT_CODE = "demo"
DEMO_DEPT_NAME = "演示部门"
DEMO_BIZ_TYPE = "leave"


class Command(BaseCommand):
    help = "生成请假申请演示数据（demo_flow_ 前缀用户，不可登录）"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先删除演示请假单与关联流程实例再生成")
        parser.add_argument("--clean-only", action="store_true", help="只清理，不生成（seed_demo_clean 编排调用）")

    # ---------------------------------------------------------------- 清理 / 用户 / 部门

    def _reset(self):
        instances = list(Leave.objects.filter(pk__in=LEAVE_PKS).values_list("instance_id", flat=True))
        deleted, _rows = Leave.objects.filter(pk__in=LEAVE_PKS).delete()
        self.stdout.write(f"removed demo leaves: {deleted}")
        if instances:
            removed, _rows = ApprovalInstance.objects.filter(pk__in=[pk for pk in instances if pk]).delete()
            self.stdout.write(f"removed demo leave instances (tasks cascade): {removed}")

    def _ensure_user(self, username: str, nickname: str) -> UserInfo:
        # all_objects：软删的演示用户同样复用（复活），避免 username 唯一约束冲突
        user = UserInfo.all_objects.filter(username=username).first()
        if user is None:
            user = UserInfo.objects.create_user(username=username, password=None, nickname=nickname)
            user.email = f"{username}@example.com"
            user.is_active = True
            user.save()
            self.stdout.write(f"created demo user: {username}")
        elif user.deleted_at or not user.is_active:
            user.deleted_at = None
            user.is_active = True
            user.save(update_fields=["deleted_at", "is_active"])
            self.stdout.write(f"restored demo user: {username}")
        return user

    def _ensure_dept(self, approver: UserInfo, applier: UserInfo) -> DeptInfo:
        """演示部门：负责人 = 演示审批人；申请人归属该部门（首节点 leader 才有候选）。"""
        dept = DeptInfo.objects.filter(code=DEMO_DEPT_CODE).first() or DeptInfo.objects.create(
            code=DEMO_DEPT_CODE, name=DEMO_DEPT_NAME
        )
        changed = []
        if dept.leader_id != approver.pk:
            dept.leader = approver
            changed.append("leader")
        if dept.name != DEMO_DEPT_NAME:
            dept.name = DEMO_DEPT_NAME
            changed.append("name")
        if changed:
            dept.save(update_fields=[*changed, "updated_time"])
            self.stdout.write(f"demo dept updated: {changed}")
        if applier.dept_id != dept.pk:
            applier.dept = dept
            applier.save(update_fields=["dept", "updated_time"])
            self.stdout.write("demo applier joined demo dept")
        return dept

    # ---------------------------------------------------------------- 演示请假单

    def _create_leaves(self, applier: UserInfo, approver: UserInfo, dept: DeptInfo):
        if Leave.objects.filter(pk__in=LEAVE_PKS).exists():
            self.stdout.write("demo leaves already exist, skip")
            return
        today = timezone.localdate()

        def day(offset: int):
            return today + datetime.timedelta(days=offset)

        plan = [
            # (固定 pk, 类型, 开始, 结束, 天数, 事由, 审批剧本, 基线时间)
            (
                LEAVE_PKS[0],
                Leave.LeaveType.ANNUAL,
                day(-20),
                day(-19),
                "2.0",
                "年假：回乡探亲（演示）",
                "approve",
                timezone.now() - datetime.timedelta(days=21),
            ),
            (
                LEAVE_PKS[1],
                Leave.LeaveType.SICK,
                day(-10),
                day(-10),
                "1.0",
                "病假：流感发热（演示）",
                "reject",
                timezone.now() - datetime.timedelta(days=11),
            ),
            (
                LEAVE_PKS[2],
                Leave.LeaveType.PERSONAL,
                day(2),
                day(4),
                "3.0",
                "事假：家中事务需处理（演示）",
                "pending",
                timezone.now() - datetime.timedelta(hours=6),
            ),
            (
                LEAVE_PKS[3],
                Leave.LeaveType.COMP_TIME,
                day(12),
                day(12),
                "1.0",
                "调休：周末加班补休（演示，未提交）",
                "draft",
                timezone.now() - datetime.timedelta(hours=2),
            ),
        ]
        for pk, leave_type, start, end, days, reason, action, baseline in plan:
            leave = Leave.objects.create(
                pk=pk,
                leave_type=leave_type,
                start_date=start,
                end_date=end,
                days=days,
                reason=reason,
                status=Leave.Status.DRAFT,
                creator=applier,
                modifier=applier,
                dept_belong=dept,
            )
            Leave.objects.filter(pk=pk).update(created_time=baseline, updated_time=baseline)
            if action == "draft":
                self.stdout.write(f"demo leave draft ready: {reason}")
                continue

            ok, error = submit_leave(leave, applier)
            if not ok:
                self.stdout.write(self.style.WARNING(f"skip demo leave {pk}: {error}"))
                continue
            leave.refresh_from_db()
            ApprovalInstance.objects.filter(pk=leave.instance_id).update(created_time=baseline)
            ApprovalNodeTask.objects.filter(instance_id=leave.instance_id).update(created_time=baseline)
            self._drive(leave, approver, action, baseline)
            leave.refresh_from_db()
            self.stdout.write(f"demo leave ready: {leave.approval_title} [{leave.status}]")

    def _drive(self, leave: Leave, approver: UserInfo, action: str, baseline):
        """按剧本推进（真实引擎）：approve / reject / pending（停首节点待办）。"""
        from approval.utils.approval_flow import approve_task, reject_task

        task = ApprovalNodeTask.objects.filter(
            instance_id=leave.instance_id, status=ApprovalNodeTask.Status.PENDING, assignee=approver
        ).first()
        if task is None:
            return
        acted = baseline + datetime.timedelta(hours=3)
        if action == "approve":
            ok, _detail = approve_task(task.pk, approver, "情况属实，同意（演示）")
        elif action == "reject":
            ok, _detail = reject_task(task.pk, approver, "请补充病假证明材料后重新提交（演示）")
        else:
            return
        if ok:
            ApprovalNodeTask.objects.filter(pk=task.pk).update(acted_at=acted)
            ApprovalInstance.objects.filter(pk=leave.instance_id).update(finished_at=acted, updated_time=acted)

    # ---------------------------------------------------------------- 入口

    def handle(self, *args, **options):
        if options["reset"] or options.get("clean_only"):
            self._reset()
        if options.get("clean_only"):
            self.stdout.write("seed_demo_leave clean-only done")
            return

        applier = self._ensure_user(DEMO_APPLIER, "演示申请人-李莉")
        approver = self._ensure_user(DEMO_APPROVER, "演示审批人-陈工")
        dept = self._ensure_dept(approver, applier)
        self._create_leaves(applier, approver, dept)
        self.stdout.write(self.style.SUCCESS(f"seed_demo_leave done (biz_type={DEMO_BIZ_TYPE})"))
