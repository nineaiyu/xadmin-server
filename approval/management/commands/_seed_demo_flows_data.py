#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批演示数据的常量与生成实现（自 seed_demo_flows 拆分，行为不变）。

含演示常量与 DemoDataMixin：演示用户保障 / 流程定义审批人改写 / 流程实例
（真实引擎推进）/ 轻量审批单 / 表单提交；命令入口与清理逻辑见 seed_demo_flows.py。
"""

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from approval.models import ApprovalInstance, ApprovalNodeTask, ApprovalRequest
from approval.utils.approval_flow import approve_task, create_instance, reject_task
from approval.utils.approval_flow.versioning import apply_definition
from system.services import DeptInfo, UserInfo

# 内置示例流程 code（loadjson/approvalflow.json）
FLOW_CODES = ("demo_leave", "demo_expense")
# 演示用户：申请人 / 审批人。**必须可登录**（与其他演示账号同口径）：演示实例的
# 在途单需要本人处理/撤回——不可登录账号会让演示单永久卡死，并因「在途实例存在时
# 流程节点不可编辑」把演示流程一并锁死（历史教训：unusable password 曾造成
# 演示审批链路在 UI 上完全无法闭环）。
DEMO_APPLIER = "demo_flow_lily"
DEMO_APPROVER = "demo_flow_chen"
# 演示账号统一密码（与 seed_demo_org 的业务演示账号一致，文档 deployment.md 同步登记）
DEMO_PASSWORD = "Demo@2026!"
# 报销类实例申请人：场景模板（seed_demo_org）创建的示例员工——其部门主管为 demo_lead，
# 满足「部门主管审批」节点「申请人有部门且主管非本人」的解析条件
DEMO_STAFF = "demo_staff"
# 节点审批人改写目标：双用户互审——任一演示用户发起，另一人必有待办
DEMO_ASSIGNEE_VALUE = f"{DEMO_APPLIER},{DEMO_APPROVER}"

# 演示数据固定 pk（6eed 段与 loadjson 的 5eed 段区分）：幂等 + 可精确清理
INSTANCE_PKS = [f"6eed0001-0000-4000-8000-00000000000{i}" for i in range(1, 6)]
REQUEST_PKS = [f"6eed0002-0000-4000-8000-00000000000{i}" for i in range(1, 6)]
SUBMISSION_PKS = [f"6eed0003-0000-4000-8000-00000000000{i}" for i in range(1, 3)]
ALL_DEMO_PKS = INSTANCE_PKS + REQUEST_PKS + SUBMISSION_PKS


class DemoDataMixin:
    """演示数据生成实现（self 为 Command：stdout / style 由 BaseCommand 提供）。"""

    if TYPE_CHECKING:  # 宿主 Command 提供的接口（mixin 模式）
        from django.core.management.base import OutputWrapper
        from django.core.management.color import Style

        stdout: OutputWrapper
        style: Style

    def _ensure_user(self, username: str, nickname: str) -> UserInfo:
        # all_objects：软删的演示用户同样复用（复活），避免 username 唯一约束冲突
        user = UserInfo.all_objects.filter(username=username).first()
        if user is None:
            user = UserInfo.objects.create_user(username=username, password=DEMO_PASSWORD, nickname=nickname)
            user.email = f"{username}@example.com"
            user.is_active = True
            dept = DeptInfo.objects.filter(code="demo").first()
            if dept:
                user.dept = dept
            user.save()
            self.stdout.write(f"created demo user: {username}")
        elif user.deleted_at or not user.is_active:
            user.deleted_at = None
            user.is_active = True
            user.save(update_fields=["deleted_at", "is_active"])
            self.stdout.write(f"restored demo user: {username}")
        # 历史版本以 unusable password 创建（不可登录）：升级为可登录演示账号，
        # 否则演示在途单的申请人/审批人无法登录处理，示例数据永久卡死
        if not user.has_usable_password():
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
            self.stdout.write(f"enable login for demo user: {username}")
        return user

    def _rebind_assignees(self):
        """把内置流程节点审批人改写为演示用户并落新版本快照（幂等：值相同不落版）。

        走版本化路径（生效行收口 + 新版本落行）：在途单按自身钉住的版本仍读旧行，
        不会被演示重绑改写；``snapshot_upsert`` 兼容重灌种子场景（loaddata 会把
        ``flow.version`` 重置回种子值，同一版本号可能已有快照）。
        """
        from approval.models import ApprovalFlow, ApprovalFlowNode

        for flow in ApprovalFlow.objects.filter(code__in=FLOW_CODES):
            targets = list(flow.nodes.filter(assignee_type=ApprovalFlowNode.AssigneeType.USER))
            if not targets:
                continue
            if all(node.assignee_value == DEMO_ASSIGNEE_VALUE for node in targets):
                continue
            target_pks = {node.pk for node in targets}
            specs = [
                {
                    "name": node.name,
                    "order": node.order,
                    "approve_type": node.approve_type,
                    "approve_ratio": node.approve_ratio,
                    "assignee_type": node.assignee_type,
                    "assignee_value": DEMO_ASSIGNEE_VALUE if node.pk in target_pks else node.assignee_value,
                    "condition": node.condition or {},
                    "routes": node.routes or [],
                    "layout": node.layout or {},
                    "timeout_hours": node.timeout_hours,
                    "cc_users": node.cc_users or [],
                }
                for node in flow.nodes.order_by("order")
            ]
            new_version = apply_definition(flow, specs, remark="演示审批人配置", snapshot_upsert=True)
            self.stdout.write(f"rebind assignees: {flow.code} -> v{new_version}")

    # ---------------------------------------------------------------- 流程实例（真实引擎推进）

    def _pending_task(self, instance: ApprovalInstance, assignee: UserInfo) -> ApprovalNodeTask:
        return instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING, assignee=assignee).first()

    def _create_demo_instances(self, applier: UserInfo, approver: UserInfo):
        from approval.models import ApprovalFlow

        if ApprovalInstance.objects.filter(pk__in=INSTANCE_PKS).exists():
            self.stdout.write("demo instances already exist, skip")
            return

        leave = ApprovalFlow.objects.filter(code="demo_leave").first()
        expense = ApprovalFlow.objects.filter(code="demo_expense").first()
        if not (leave and expense):
            self.stdout.write(self.style.WARNING("demo flows missing (run load_init_json first), skip instances"))
            return

        # 报销类实例的申请人必须有部门且部门主管非本人：场景模板的「部门主管审批」节点按
        # 申请人部门动态解析审批人，超管（无部门）或部门主管自己发起都会无人可审（引擎
        # fail-closed 拒绝），故统一由示例员工（seed_demo_org 创建，主管为 demo_lead）发起。
        staff = UserInfo.objects.filter(username=DEMO_STAFF).first()
        if staff is None:
            self.stdout.write(
                self.style.WARNING(f"{DEMO_STAFF} missing (run seed_demo_org first); expense instances skipped")
            )

        now = timezone.now()
        # 注意：form_data 必须与流程「当前」form_schema 的必填字段对齐——
        # demo_expense 的 schema 会被 seed_demo_org 场景模板改写为 amount + reason(必填)，
        # 若仍按 loadjson 原始字段（invoice_no/remark）构造，实例会被引擎校验拒绝而跳过
        plan = [
            # (固定 pk, 流程, 申请人, 标题, form_data, 基线时间, 推进脚本)
            (
                INSTANCE_PKS[0],
                leave,
                applier,
                "李莉的请假申请（演示）",
                {"days": 3, "reason": "家中有事，需回老家三天"},
                now - timedelta(hours=6),
                "pending_second",  # 首节点通过后停在第二节点：审批人有待办
            ),
            (
                INSTANCE_PKS[1],
                expense,
                staff,
                "员工的差旅报销（演示）",
                {"amount": 680, "reason": "上海出差餐费与市内交通"},
                now - timedelta(days=5),
                "approve_all",  # 金额 <1000 不经财务复核：部门主管通过即终态
            ),
            (
                INSTANCE_PKS[2],
                expense,
                staff,
                "员工的设备采购报销（演示）",
                {"amount": 5200, "reason": "测试设备采购（性能压测机）"},
                now - timedelta(days=3),
                "approve_all",  # 金额 ≥1000：部门主管 + 财务复核两级全通过
            ),
            (
                INSTANCE_PKS[3],
                leave,
                applier,
                "李莉的年假申请（演示）",
                {"days": 5, "reason": "年假旅行"},
                now - timedelta(days=2),
                "reject",  # 首节点驳回：终态
            ),
            (
                INSTANCE_PKS[4],
                expense,
                staff,
                "员工的办公用品报销（演示）",
                {"amount": 1200, "reason": "部门办公耗材采购"},
                now - timedelta(hours=2),
                "pending_second",  # 部门主管通过后停在财务复核：审批人有待办
            ),
        ]
        if staff is None:
            plan = [row for row in plan if row[1] is leave]
        for pk, flow, applicant, title, form_data, baseline, action in plan:
            instance, error = create_instance(flow=flow, applicant=applicant, title=title, form_data=form_data)
            if error:
                self.stdout.write(self.style.WARNING(f"skip instance {title}: {error}"))
                continue
            # 引擎造的实例是随机 uuid，改绑为固定 pk 以便幂等与清理
            ApprovalInstance.objects.filter(pk=instance.pk).update(created_time=baseline)
            ApprovalNodeTask.objects.filter(instance=instance).update(created_time=baseline)
            instance = self._migrate_pk(instance, pk)
            self._drive(instance, applicant, approver, action, baseline)
            self.stdout.write(f"demo instance ready: {title} [{ApprovalInstance.objects.get(pk=pk).status}]")

    @staticmethod
    def _migrate_pk(instance: ApprovalInstance, target_pk: str) -> ApprovalInstance:
        """把引擎生成的实例与任务改绑到固定 pk（保持演示数据可幂等重建）。

        必须整体包进事务：Postgres 对实例主键的 UPDATE 会立即做 FK 检查
        （任务行仍引用旧 pk），依赖 Django FK 约束的 DEFERRABLE INITIALLY
        DEFERRED 语义，同一事务内同步改绑任务后提交才不会违反约束。
        """
        if str(instance.pk) == target_pk:
            return instance
        with transaction.atomic():
            ApprovalInstance.objects.filter(pk=instance.pk).update(id=target_pk)
            ApprovalNodeTask.objects.filter(instance_id=instance.pk).update(instance_id=target_pk)
        return ApprovalInstance.objects.get(pk=target_pk)

    def _drive(self, instance: ApprovalInstance, applicant: UserInfo, approver: UserInfo, action: str, baseline):
        """按剧本推进演示实例（真实引擎：approve_task / reject_task）。"""
        offset = timedelta(minutes=30)

        def stamp():
            nonlocal offset
            offset += timedelta(minutes=10)
            return baseline + offset

        if action == "approve_all":
            # 逐节点通过：以「当前待办任务的真实处理人」执行审批——场景模板首节点是
            # 按申请人部门动态解析的「部门主管」，不能假定为固定的演示审批人
            while True:
                task = instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).first()
                if task is None:
                    break
                ok, _detail = approve_task(task.pk, task.assignee, "情况属实，同意（演示）")
                if not ok:
                    break
                acted = stamp()
                ApprovalNodeTask.objects.filter(pk=task.pk).update(acted_at=acted)
                instance.refresh_from_db()
                if instance.status != ApprovalInstance.Status.PENDING:
                    break
            if instance.status == ApprovalInstance.Status.APPROVED:
                ApprovalInstance.objects.filter(pk=instance.pk).update(finished_at=stamp())
        elif action == "reject":
            task = instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING, assignee=approver).first()
            if task is not None:
                ok, _detail = reject_task(task.pk, approver, "事由不充分，请补充材料（演示）")
                if ok:
                    acted = stamp()
                    ApprovalNodeTask.objects.filter(pk=task.pk).update(acted_at=acted)
                    ApprovalInstance.objects.filter(pk=instance.pk).update(finished_at=acted)
        elif action == "pending_second":
            # 首节点通过后停在第二节点（审批人有待办）。任务定位不限定处理人：
            # 互审配置下首节点审批人可能是申请人侧演示用户（如陈工发起、李莉初审）
            task = instance.tasks.filter(status=ApprovalNodeTask.Status.PENDING).first()
            if task is not None:
                ok, _detail = approve_task(task.pk, task.assignee, "初审通过（演示）")
                if ok:
                    ApprovalNodeTask.objects.filter(pk=task.pk).update(acted_at=stamp())

    # ---------------------------------------------------------------- 轻量审批单 / 表单提交

    def _create_demo_requests(self, applier: UserInfo, approver: UserInfo):
        if ApprovalRequest.objects.filter(pk__in=REQUEST_PKS).exists():
            self.stdout.write("demo approval requests already exist, skip")
            return
        now = timezone.now()
        user_path = f"/api/system/user/{uuid.uuid4()}"
        rows = [
            # (固定 pk, 状态, method, path, object_pk, params, approver, 基线)
            (
                REQUEST_PKS[0],
                ApprovalRequest.Status.PENDING,
                "DELETE",
                user_path,
                user_path.rsplit("/", 1)[-1],
                {"username": "demo_fired_user", "nickname": "演示离职账号"},
                None,
                now - timedelta(hours=4),
            ),
            (
                REQUEST_PKS[1],
                ApprovalRequest.Status.APPROVED,
                "POST",
                "/api/dataset/dynamic-form-submissions",
                None,
                {"form_name": "示例-入职信息登记", "name": "临时人员（演示）"},
                approver,
                now - timedelta(days=3),
            ),
            (
                REQUEST_PKS[2],
                ApprovalRequest.Status.REJECTED,
                "DELETE",
                user_path,
                user_path.rsplit("/", 1)[-1],
                {"username": "demo_fired_user", "nickname": "演示离职账号"},
                approver,
                now - timedelta(days=2),
            ),
            (
                REQUEST_PKS[3],
                ApprovalRequest.Status.CANCELLED,
                "DELETE",
                f"/api/system/role/{uuid.uuid4()}",
                None,
                {"code": "demo_removed_role"},
                None,
                now - timedelta(days=1),
            ),
            (
                REQUEST_PKS[4],
                ApprovalRequest.Status.EXPIRED,
                "DELETE",
                user_path,
                user_path.rsplit("/", 1)[-1],
                {"username": "demo_fired_user", "nickname": "演示离职账号"},
                None,
                now - timedelta(days=6),
            ),
        ]
        for pk, status, method, path, object_pk, params, approver_ref, baseline in rows:
            ApprovalRequest.objects.create(
                pk=pk,
                creator=applier,
                module="演示：敏感操作审批",
                method=method,
                path=path,
                object_pk=object_pk or "",
                params=params,
                status=status,
                approver=approver_ref,
                reason="不在变更窗口，请工作日重新提交（演示）" if status == ApprovalRequest.Status.REJECTED else None,
                approved_at=baseline + timedelta(minutes=20) if approver_ref else None,
                expired_at=baseline + timedelta(minutes=50) if approver_ref else None,
            )
            ApprovalRequest.objects.filter(pk=pk).update(created_time=baseline, updated_time=baseline)
        self.stdout.write(f"demo approval requests created: {len(rows)}")

    def _create_demo_submissions(self, applier: UserInfo):
        from dataset.models import DynamicForm, DynamicFormSubmission
        from dataset.utils.dform import validate_submission_data

        if DynamicFormSubmission.objects.filter(pk__in=SUBMISSION_PKS).exists():
            self.stdout.write("demo form submissions already exist, skip")
            return
        onboarding = DynamicForm.objects.filter(name="示例-入职信息登记").first()
        device = DynamicForm.objects.filter(name="示例-设备领用申请").first()
        rows = []
        if onboarding:
            rows.append(
                (
                    SUBMISSION_PKS[0],
                    onboarding,
                    validate_submission_data(
                        onboarding.schema,
                        {
                            "name": "李莉",
                            "phone": "13655556666",
                            "hire_date": "2026-09-18",
                            "dept": "人事部",
                            "self_intro": "负责招聘与员工关系。",
                            "need_laptop": True,
                        },
                    ),
                )
            )
        if device:
            rows.append(
                (
                    SUBMISSION_PKS[1],
                    device,
                    validate_submission_data(
                        device.schema,
                        {
                            "device_model": "MacBook Pro 14",
                            "quantity": 1,
                            "purpose": "日常办公",
                            "expect_date": "2026-09-19",
                        },
                    ),
                )
            )
        now = timezone.now()
        for index, (pk, form, data) in enumerate(rows):
            DynamicFormSubmission.objects.create(pk=pk, form=form, data=data, creator=applier)
            baseline = now - timedelta(days=1, hours=index)
            DynamicFormSubmission.objects.filter(pk=pk).update(created_time=baseline)
        self.stdout.write(f"demo form submissions created: {len(rows)}")
