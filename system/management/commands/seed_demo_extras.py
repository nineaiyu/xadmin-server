#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补充演示数据：岗位 / 按岗位解析的演示审批流 / 标签打标 / AI 助手会话 / 导出中心记录。

``seed_demo_all`` 的其余步骤已把组织、审批、请假、内容铺开，但以下页面仍是空的：

- **岗位管理**：``Post`` 表零数据，且 ``UserInfo.posts`` 从未被赋值；
- **标签页**：内置 3 个标签存在，却没有任何对象被打标（看不出打标能力）；
- **AI 助手**：``AiChatMessage`` 为空，助手页历史一片空白；
- **导出中心**：``ExportRecord`` 为空，看不到成功/进行中/失败的导出形态。

本命令只补这些「有真实语义、造假不污染系统」的实例数据。审计/实时类数据
（操作日志、登录日志、在线用户、任务日志、账户风险）**刻意不造**：它们由真实
请求/调度生成，伪造会让审计失真、在线/风险语义自相矛盾。

幂等：岗位按固定 code、打标按 (标签, 对象) 唯一键、AI 消息带 ``extra.demo`` 标记、
导出记录按固定 pk 更新；清理：``--clean-only`` 按同一批标识物理解除。

用法：

    python manage.py seed_demo_extras                # 幂等生成
    python manage.py seed_demo_extras --reset        # 先清理再生成
    python manage.py seed_demo_extras --clean-only   # 只清理（seed_demo_clean 编排调用）
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils import timezone

from ai.utils.ai_chat import persist_message
from approval.models import ApprovalInstance
from identity.models import DeptInfo, Post, UserInfo
from system.management.commands.seed_demo_admin import ADMIN_USERNAME
from system.models import ExportRecord, Tag, TaggedItem, UploadFile

#: 演示岗位固定 code 前缀（幂等定位 / 精确清理）
POST_CODE_PREFIX = "demo_post_"
#: (code, 名称, 部门 code 或 None, 排序, 是否启用)
POST_PLAN = (
    ("demo_post_security_officer", "示例-安全员（全组织）", None, 10, True),
    ("demo_post_tech_lead", "示例-研发主管", "demo_rd", 20, True),
    ("demo_post_backend", "示例-后端工程师", "demo_rd", 30, True),
    ("demo_post_finance", "示例-财务专员", "demo_fin", 25, True),
    ("demo_post_intern", "示例-实习生（停用）", "demo_rd", 90, False),
)
#: 演示用户 → 岗位（一人可兼多岗）
POST_ASSIGN = {
    "admin": ("demo_post_security_officer", "demo_post_tech_lead"),
    "demo_lead": ("demo_post_tech_lead",),
    "demo_staff": ("demo_post_backend",),
    "demo_fin": ("demo_post_finance",),
}
#: 按岗位解析审批人的演示流程：首节点 = 安全员岗（demo_post_security_officer），
#  展示「指定岗位」审批人类型（岗位为人员维度，不参与权限判定）
DEMO_POST_FLOW_CODE = "demo_post_review"
#: 导出记录固定 pk（6eed 段与既有演示数据区分）+ 三种状态形态
EXPORT_PKS = (
    "6eed000c-0000-4000-8000-000000000001",
    "6eed000c-0000-4000-8000-000000000002",
    "6eed000c-0000-4000-8000-000000000003",
)
EXPORT_PLAN = (
    {
        "name": "演示-用户数据导出.xlsx",
        "module": "系统管理 / 用户",
        "path": "/api/system/user",
        "file_format": "xlsx",
        "status": ExportRecord.Status.SUCCESS,
        "rows": 128,
        "progress": 100,
        "stage": "导出完成",
        "error": None,
    },
    {
        "name": "演示-审批实例导出.xlsx",
        "module": "审批中心 / 流程实例",
        "path": "/api/approval/approval-instances",
        "file_format": "xlsx",
        "status": ExportRecord.Status.RUNNING,
        "rows": None,
        "progress": 45,
        "stage": "渲染内容",
        "error": None,
    },
    {
        "name": "演示-操作日志导出.csv",
        "module": "系统管理 / 操作日志",
        "path": "/api/system/logs/operation",
        "file_format": "csv",
        "status": ExportRecord.Status.FAILURE,
        "rows": None,
        "progress": 0,
        "stage": "",
        "error": "导出数据量超出限制，请缩小筛选范围（演示失败样例）",
    },
)
#: AI 助手会话（feature, role, content, extra）；同一 (creator, feature) 构成一条消息流
AI_MESSAGE_PLAN: tuple[tuple[str, str, str, dict], ...] = (
    (
        "docs",
        "user",
        "新员工入职当天需要办理哪些事项？",
        {"demo": True},
    ),
    (
        "docs",
        "assistant",
        "按《新员工入职指南》：1) 到行政部领取办公设备并登记工位；2) 登录 xAdmin 完善个人资料、绑定邮箱；"
        "3) 由直属主管分配部门与角色权限。常用入口：请假申请、表单填报、审批中心。",
        {"demo": True, "sources": ["演示-新员工入职指南"]},
    ),
    (
        "nl",
        "user",
        "近一年每月新增用户有多少？",
        {"demo": True},
    ),
    (
        "nl",
        "assistant",
        "近 12 个月新增用户呈平稳分布（示例-全部用户数据集按月聚合），可切换到「数据集 / 仪表盘」查看趋势卡。",
        {"demo": True, "columns": ["月份", "新增用户数"], "rows": []},
    ),
)


class Command(BaseCommand):
    help = "补充演示数据：岗位/标签打标/AI 助手会话/导出中心记录"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先清理本命令生成的演示数据再生成")
        parser.add_argument("--clean-only", action="store_true", help="只清理，不生成（seed_demo_clean 编排调用）")

    def handle(self, *args, **options):
        if options["reset"] or options["clean_only"]:
            self._clean()
            if options["clean_only"]:
                return

        # 只认演示账号（非超管同名）：同名真实超管不挂演示数据
        admin = UserInfo.objects.filter(username=ADMIN_USERNAME, is_superuser=False).first()
        self._ensure_posts()
        self._assign_posts()
        self._ensure_post_flow()
        self._assign_tags()
        self._create_ai_history(admin)
        self._create_exports(admin)
        self.stdout.write(self.style.SUCCESS("seed_demo_extras done"))

    # ---------------------------------------------------------------- 清理

    def _clean(self):
        from approval.models import ApprovalFlow

        removed = Post.all_objects.filter(code__startswith=POST_CODE_PREFIX).delete()[0]
        self.stdout.write(f"removed demo posts: {removed}")

        # 演示流程按固定 code 定位（节点随 FK 级联删除）
        flow = ApprovalFlow.objects.filter(code=DEMO_POST_FLOW_CODE).first()
        if flow is not None:
            flow.delete()
            self.stdout.write(f"removed demo post flow: {DEMO_POST_FLOW_CODE}")

        object_ids = self._demo_tag_object_ids()
        removed = TaggedItem.objects.filter(tag__builtin=True, object_id__in=object_ids).delete()[0]
        self.stdout.write(f"removed demo taggings: {removed}")

        # AI 消息用 extra.demo 标记定位：避免按用户名 admin 误删真实超管的历史
        from ai.models.ai import AiChatMessage

        removed = AiChatMessage.objects.filter(extra__demo=True).delete()[0]
        self.stdout.write(f"removed demo AI chat messages: {removed}")

        removed = ExportRecord.objects.filter(pk__in=EXPORT_PKS).delete()[0]
        self.stdout.write(f"removed demo export records: {removed}")

    def _demo_tag_object_ids(self) -> list:
        """演示打标对象的 object_id 集合（清理依据，须在对象被清前运行）。"""
        from approval.management.commands.seed_demo_flows import INSTANCE_PKS

        ids = set(INSTANCE_PKS)
        ids.update(str(pk) for pk in self._demo_admin_pks())
        ids.update(
            str(pk) for pk in UploadFile.all_objects.filter(filename__startswith="演示-").values_list("pk", flat=True)
        )
        ids.update(
            str(pk) for pk in UserInfo.all_objects.filter(username__startswith="demo_").values_list("pk", flat=True)
        )
        return list(ids)

    @staticmethod
    def _demo_admin_pks():
        # 仅取非超管的同名 account（超管同名账号不是演示账号，不打标也不清理）
        return UserInfo.all_objects.filter(username=ADMIN_USERNAME, is_superuser=False).values_list("pk", flat=True)

    # ---------------------------------------------------------------- 岗位

    def _ensure_posts(self):
        for code, name, dept_code, rank, is_active in POST_PLAN:
            dept = DeptInfo.objects.filter(code=dept_code).first() if dept_code else None
            Post.objects.update_or_create(
                code=code,
                defaults={"name": name, "dept": dept, "rank": rank, "is_active": is_active},
            )
        self.stdout.write(f"demo posts ready: {Post.objects.filter(code__startswith=POST_CODE_PREFIX).count()}")

    @staticmethod
    def _ensure_post_flow():
        """含「指定岗位」节点的演示流程（幂等：按 code update_or_create）。

        只建定义不造实例：安全员岗的持岗人（含真实 admin）随部署而异，实例由
        使用者在流程审批页真实发起，确保审批轨迹可信。
        """
        from approval.models import ApprovalFlow, ApprovalFlowNode

        flow, _created = ApprovalFlow.objects.update_or_create(
            code=DEMO_POST_FLOW_CODE,
            defaults={
                "name": "示例-安全事项审批",
                "is_active": True,
                "form_schema": [
                    {"key": "summary", "label": "事项说明", "type": "textarea", "required": True, "options": []}
                ],
            },
        )
        ApprovalFlowNode.objects.update_or_create(
            flow=flow,
            order=1,
            defaults={
                "name": "安全员审批",
                "assignee_type": ApprovalFlowNode.AssigneeType.POST,
                "assignee_value": "demo_post_security_officer",
            },
        )

    def _assign_posts(self):
        assigned = 0
        for username, codes in POST_ASSIGN.items():
            user = UserInfo.objects.filter(username=username, is_superuser=False).first()
            if user is None:
                continue
            posts = list(Post.objects.filter(code__in=codes))
            user.posts.set(posts)
            assigned += 1
        self.stdout.write(f"demo users with posts: {assigned}")

    # ---------------------------------------------------------------- 标签打标

    def _assign_tags(self):
        plans = [
            ("待跟进", ApprovalInstance, "6eed0001-0000-4000-8000-000000000001"),
            ("归档", ApprovalInstance, "6eed0001-0000-4000-8000-000000000004"),
        ]
        admin = UserInfo.objects.filter(username=ADMIN_USERNAME, is_superuser=False).first()
        if admin is not None:
            plans.append(("重点", UserInfo, admin.pk))
        sample_file = UploadFile.all_objects.filter(filename__startswith="演示-").first()
        if sample_file is not None:
            plans.append(("重点", UploadFile, sample_file.pk))

        created = 0
        for tag_name, model, object_id in plans:
            tag = Tag.objects.filter(name=tag_name).first()
            if tag is None:
                continue
            content_type = ContentType.objects.get_for_model(model)
            _item, is_created = TaggedItem.objects.get_or_create(
                tag=tag, content_type=content_type, object_id=str(object_id)
            )
            created += 1 if is_created else 0
        self.stdout.write(f"demo taggings created: {created}")

    # ---------------------------------------------------------------- AI 助手会话

    def _create_ai_history(self, admin: UserInfo | None):
        if admin is None:
            self.stdout.write(self.style.WARNING("demo admin missing (run seed_demo_admin first); AI history skipped"))
            return
        if self._has_demo_ai_history(admin):
            self.stdout.write("demo AI history already exists, skip")
            return
        base = timezone.now() - timedelta(hours=3)
        for index, (feature, role, content, extra) in enumerate(AI_MESSAGE_PLAN):
            message = persist_message(admin, feature, role, content=content, extra=extra)
            if message is not None:
                stamp = base + timedelta(minutes=index)
                message.created_time = stamp
                message.save(update_fields=["created_time"])
        self.stdout.write("demo AI history ready")

    @staticmethod
    def _has_demo_ai_history(admin: UserInfo) -> bool:
        from ai.models.ai import AiChatMessage

        return AiChatMessage.objects.filter(creator=admin, extra__demo=True).exists()

    # ---------------------------------------------------------------- 导出中心

    def _create_exports(self, admin: UserInfo | None):
        now = timezone.now()
        for index, pk in enumerate(EXPORT_PKS):
            ExportRecord.objects.update_or_create(
                pk=pk,
                defaults={
                    "name": EXPORT_PLAN[index]["name"],
                    "module": EXPORT_PLAN[index]["module"],
                    "path": EXPORT_PLAN[index]["path"],
                    "file_format": EXPORT_PLAN[index]["file_format"],
                    "status": EXPORT_PLAN[index]["status"],
                    "rows": EXPORT_PLAN[index]["rows"],
                    "progress": EXPORT_PLAN[index]["progress"],
                    "stage": EXPORT_PLAN[index]["stage"],
                    "error": EXPORT_PLAN[index]["error"],
                    "creator": admin,
                    "modifier": admin,
                    "params": {"demo": True},
                },
            )
            ExportRecord.objects.filter(pk=pk).update(created_time=now - timedelta(hours=index + 1))
        self.stdout.write(f"demo export records ready: {len(EXPORT_PKS)}")
