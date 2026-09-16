#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开箱模板：示例组织 + 预置角色（四层权限）+ 场景模板（请假 / 报销 / 入职登记）。

新装系统默认只有一个超管，审批类功能因缺少「组织 / 角色 / 字段权限 / 数据权限」
四层配置而跑不起来。本命令一次性建齐这些依赖，让管理员在几分钟内跑通第一单：

- 组织：研发部（含主管）+ 财务部，三个示例账号（主管 / 员工 / 财务）；
- 角色：示例-员工、示例-主管（菜单授权 + 权限点 + 字段权限 + 数据权限）；
- 场景：报销审批流程、入职登记表（绑定审批流程，演示表单与流程引擎的联动）。

用法：``python manage.py seed_demo_org [--reset] [--password xxx]``。
``--reset`` 会先清理本命令创建的示例数据（按 demo_ 前缀与固定编码）再重建；
重复执行按编码/用户名幂等更新，不产生重复数据。
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from system.models import (
    DataPermission,
    DeptInfo,
    DynamicForm,
    FieldPermission,
    Menu,
    ModelLabelField,
    UserInfo,
    UserRole,
)
from system.models.approval import ApprovalFlow, ApprovalFlowNode

DEFAULT_PASSWORD = "Demo@2026!"

# 示例组织：部门（code, 名称, 主管用户名）
DEPTS = [
    ("demo_rd", "示例-研发部", "demo_lead"),
    ("demo_fin", "示例-财务部", "demo_fin"),
]
# 示例账号（username, 昵称, 部门 code, 角色 code, 手机号）
USERS = [
    ("demo_lead", "示例-张主管（研发部主管）", "demo_rd", "demo_leader_role", "13800000011"),
    ("demo_staff", "示例-李员工（研发部员工）", "demo_rd", "demo_staff_role", "13800000012"),
    ("demo_fin", "示例-王财务（财务部审批）", "demo_fin", "demo_leader_role", "13800000013"),
]
# 预置角色的页面授权（员工：发起/填报；主管：审批/待办）
ROLE_MENUS = {
    "demo_staff_role": [
        "/form-collection/my/index",
        "/system/approval/instance/index",
        "/user/notice/index",
        "/system/leave/index",
    ],
    "demo_leader_role": [
        "/system/approval/index",
        "/system/approval/instance/index",
        "/user/notice/index",
    ],
}
ROLE_NAMES = {
    "demo_staff_role": "示例-员工",
    "demo_leader_role": "示例-主管",
}
# 需要放行的模型（字段权限按模型全字段，数据权限按 value.all 全量放行）
GRANT_MODELS = [
    "system.approvalflow",
    "system.approvalinstance",
    "system.approvalrequest",
    "system.dynamicform",
    "system.dynamicformsubmission",
    "system.leave",
]


def _page_menus(paths):
    """按页面 path 收集菜单项：页面本身 + 其下权限点 + 上级目录。"""
    menus = []
    pages = Menu.objects.filter(path__in=paths, menu_type=Menu.MenuChoices.MENU)
    for page in pages:
        menus.append(page)
        if page.parent_id:
            menus.append(page.parent)
        menus.extend(Menu.objects.filter(parent=page, menu_type=Menu.MenuChoices.PERMISSION))
    return list({item.pk: item for item in menus}.values())


class Command(BaseCommand):
    help = "创建开箱模板：示例组织 + 预置角色（四层权限）+ 场景模板（请假/报销/入职登记）"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="先清理本命令创建的示例数据再重建")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="示例账号初始密码")

    def handle(self, *args, **options):
        self.password = options.get("password") or DEFAULT_PASSWORD
        with transaction.atomic():
            if options.get("reset"):
                self._clean()
            depts = self._ensure_org()
            roles = self._ensure_roles()
            self._ensure_grants(roles)
            self._bind_users(roles, depts)
            self._ensure_scenes()
        self.stdout.write(self.style.SUCCESS(self._summary()))

    # ---------- 清理（reset） ----------
    def _clean(self):
        # 用户为软删模型：走 all_objects 硬删，否则软删行仍持有部门外键，
        # 后续删除部门会被 UserInfo.dept 的 PROTECT 约束挡住
        UserInfo.all_objects.filter(username__in=[u[0] for u in USERS]).delete()
        DeptInfo.objects.filter(code__in=[d[0] for d in DEPTS]).delete()
        UserRole.objects.filter(code__in=list(ROLE_NAMES)).delete()
        DataPermission.objects.filter(name__startswith="示例-").delete()
        ApprovalFlow.objects.filter(code__in=["demo_expense", "demo_onboarding"]).delete()
        DynamicForm.objects.filter(name__in=["示例-入职登记表"]).delete()
        self.stdout.write("已清理既有示例数据")

    # ---------- 组织 ----------
    def _ensure_org(self):
        depts = {}
        for code, name, _leader in DEPTS:
            dept, _ = DeptInfo.objects.update_or_create(
                code=code, defaults={"name": name, "rank": 900, "is_active": True}
            )
            depts[code] = dept
        for username, nickname, dept_code, _role, phone in USERS:
            user, created = UserInfo.objects.get_or_create(
                username=username,
                defaults={
                    "nickname": nickname,
                    "dept": depts[dept_code],
                    "is_active": True,
                    "phone": phone,
                    "email": f"{username}@demo.local",
                },
            )
            if not created:
                user.nickname = nickname
                user.dept = depts[dept_code]
                user.is_active = True
                user.save(update_fields=["nickname", "dept", "is_active", "updated_time"])
            user.set_password(self.password)
            user.save(update_fields=["password", "updated_time"])
        # 部门主管：研发部主管 demo_lead、财务部 demo_fin
        for code, _name, leader_username in DEPTS:
            leader = UserInfo.objects.filter(username=leader_username).first()
            if leader:
                dept = depts[code]
                if dept.leader_id != leader.pk:
                    dept.leader = leader
                    dept.save(update_fields=["leader", "updated_time"])
        return depts

    # ---------- 角色 ----------
    def _ensure_roles(self):
        roles = {}
        for code, name in ROLE_NAMES.items():
            role, _ = UserRole.objects.update_or_create(
                code=code, defaults={"name": name, "is_active": True, "description": "开箱模板预置角色"}
            )
            role.menu.set(_page_menus(ROLE_MENUS[code]))
            roles[code] = role
        return roles

    # ---------- 字段权限 + 数据权限 ----------
    def _ensure_grants(self, roles):
        # 字段权限：角色 × 接口菜单 → 模型全字段（未配置时序列化字段被整体裁剪）
        model_fields = {}
        for model_name in GRANT_MODELS:
            root = ModelLabelField.objects.filter(name=model_name, parent__isnull=True).first()
            if root is None:
                self.stdout.write(self.style.WARNING(f"跳过字段权限：模型 {model_name} 尚未同步字段树"))
                continue
            model_fields[model_name] = list(ModelLabelField.objects.filter(parent=root))

        all_fields = [field for fields in model_fields.values() for field in fields]
        for code, role in roles.items():
            for menu in _page_menus(ROLE_MENUS[code]):
                # 目录节点不承载字段权限（请求按接口菜单匹配）
                if menu.menu_type == Menu.MenuChoices.DIRECTORY:
                    continue
                permission, _ = FieldPermission.objects.get_or_create(role=role, menu=menu)
                permission.field.set(all_fields)

        # 数据权限：全量放行规则（fail-closed：无授权则列表为空）
        for model_name in GRANT_MODELS:
            DataPermission.objects.update_or_create(
                name=f"示例-{model_name}",
                defaults={
                    "is_active": True,
                    "rules": [
                        {
                            "table": model_name,
                            "field": "id",
                            "type": "value.all",
                            "match": "all",
                            "value": "",
                            "exclude": False,
                        }
                    ],
                },
            )

    def _bind_users(self, roles, depts):
        rules = list(DataPermission.objects.filter(name__startswith="示例-"))
        for username, _nickname, _dept_code, role_code, _phone in USERS:
            user = UserInfo.objects.filter(username=username).first()
            if not user:
                continue
            user.roles.set([roles[role_code]])
            user.rules.set(rules)

    # ---------- 场景模板 ----------
    def _ensure_scenes(self):
        # 1) 报销审批流程：部门主管 → 金额 ≥1000 加签财务
        flow, _ = ApprovalFlow.objects.update_or_create(
            code="demo_expense",
            defaults={
                "name": "示例-费用报销审批",
                "is_active": True,
                "form_schema": [
                    {"key": "amount", "label": "报销金额（元）", "type": "number", "required": True},
                    {"key": "reason", "label": "报销事由", "type": "textarea", "required": True},
                ],
                "description": "开箱模板：金额 ≥1000 时追加财务审批节点",
            },
        )
        flow.nodes.all().delete()
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="部门主管审批",
            order=1,
            approve_type=ApprovalFlowNode.ApproveType.OR,
            assignee_type=ApprovalFlowNode.AssigneeType.LEADER,
        )
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="财务复核",
            order=2,
            approve_type=ApprovalFlowNode.ApproveType.OR,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="demo_fin",
            condition={"field": "amount", "op": "gte", "value": 1000},
        )

        # 2) 入职登记表：绑定「入职审批流程」，演示表单与流程引擎联动
        onboarding_flow, _ = ApprovalFlow.objects.update_or_create(
            code="demo_onboarding",
            defaults={
                "name": "示例-入职登记审批",
                "is_active": True,
                "form_schema": [{"key": "days", "label": "试用天数", "type": "number"}],
                "description": "开箱模板：入职登记表绑定的审批流程",
            },
        )
        if not onboarding_flow.nodes.exists():
            ApprovalFlowNode.objects.create(
                flow=onboarding_flow,
                name="人事确认",
                order=1,
                approve_type=ApprovalFlowNode.ApproveType.OR,
                assignee_type=ApprovalFlowNode.AssigneeType.USER,
                assignee_value="demo_fin",
            )
        DynamicForm.objects.update_or_create(
            name="示例-入职登记表",
            defaults={
                "description": "开箱模板：含附件/日期范围/明细子表三种新控件，绑定入职审批流程",
                "is_active": True,
                "approval_flow": onboarding_flow,
                "schema": {
                    "fields": [
                        {"key": "name", "label": "姓名", "type": "input", "required": True},
                        {"key": "onboard_date", "label": "入职日期", "type": "date", "required": True},
                        {"key": "probation", "label": "试用期", "type": "daterange"},
                        {"key": "certificates", "label": "证件材料", "type": "upload"},
                        {
                            "key": "education",
                            "label": "教育经历",
                            "type": "table",
                            "columns": [
                                {"key": "school", "label": "学校", "type": "input"},
                                {"key": "year", "label": "毕业年份", "type": "number"},
                                {
                                    "key": "degree",
                                    "label": "学历",
                                    "type": "select",
                                    "options": ["本科", "硕士", "博士"],
                                },
                            ],
                        },
                    ]
                },
            },
        )

    def _summary(self):
        return (
            "\n开箱模板已就绪：\n"
            f"  账号：demo_staff（员工）/ demo_lead（研发主管）/ demo_fin（财务）  密码：{self.password}\n"
            "  组织：示例-研发部（主管 demo_lead）、示例-财务部\n"
            "  角色：示例-员工 / 示例-主管（菜单 + 权限点 + 字段权限 + 数据权限已配齐）\n"
            "  场景：费用报销审批流程、入职登记表（绑定入职登记审批流程）\n"
            "  下一步：以 demo_staff 登录 → 我的填报 → 填写入职登记表 → 提交 → 以 demo_lead/demo_fin 审批"
        )
