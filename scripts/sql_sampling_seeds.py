#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQL 抽样实测的种子数据（自 sql_sampling.py 拆出，控制单文件体量）。

播种口径与触发制任务的实测说明见 ``scripts/sql_sampling.py`` 模块注释；
本模块只负责「在专用一次性测试库上构造代表量数据」：

- reset_tables：全量清表（--keepdb 复跑时保证播种幂等）；
- seed：按抽样面分域播种（块间防御式隔离：单块失败不阻断其余端点抽样），
  返回各域播种行数（covered），随报告输出供评估数据充分性。
"""

from django.db import connection


def reset_tables() -> None:
    """专用一次性测试库全量清表（--keepdb 复跑时保证播种幂等）。"""
    with connection.cursor() as cursor:
        tables = [t for t in connection.introspection.table_names() if t != "django_migrations"]
        if tables:
            quoted = ", ".join(f'"{t}"' for t in tables)
            cursor.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def _ensure_admin():
    from django.contrib.auth import get_user_model

    admin = get_user_model().objects.filter(username="admin").first()
    if admin is None:
        admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="Admin@123456"
        )
    return admin


def _seed_system_core(admin, covered: dict) -> list:
    """用户（挂部门/角色/岗位）+ 标签 + 字典 + 上传文件。"""
    from django.contrib.contenttypes.models import ContentType

    from identity.models import DeptInfo, Post, UserInfo, UserRole
    from system.models import DataDict, Tag, TaggedItem, UploadFile

    roles = [UserRole.objects.create(name=f"角色{i}", code=f"role{i}") for i in range(45)]
    depts = [DeptInfo.objects.create(name=f"部门{i}", code=f"dept{i}") for i in range(45)]
    posts = [Post.objects.create(name=f"岗位{i}", code=f"post{i}", dept=depts[i % len(depts)]) for i in range(45)]
    users = []
    for i in range(45):
        user = UserInfo.objects.create_user(username=f"sample_{i:02d}", password="Test@123456", nickname=f"样本{i}")
        user.dept = depts[i % len(depts)]
        user.save(update_fields=["dept"])
        user.roles.add(roles[i % len(roles)])
        user.posts.add(posts[i % len(posts)])
        users.append(user)
    covered["system/user"] = len(users)

    try:
        content_type = ContentType.objects.get_for_model(type(admin))
        for tag in [Tag.objects.create(name=f"标签{i}") for i in range(45)]:
            TaggedItem.objects.create(tag=tag, object_id=str(admin.pk), content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] tag 绑定跳过：{exc}")

    try:
        roots = [DataDict.objects.create(code=f"group{i}", label=f"字典组{i}") for i in range(45)]
        for root in roots:
            for j in range(8):
                DataDict.objects.create(code=f"{root.code}_{j}", label=f"字典项{j}", parent=root)
        covered["system/dict"] = 45 * 8
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] dict 跳过：{exc}")

    try:
        UploadFile.objects.bulk_create(
            [
                UploadFile(
                    filename=f"样本文件{i}.txt",
                    filesize=1024 * i,
                    mime_type="text/plain",
                    md5sum=f"md5-sample-{i:04d}",
                    creator=users[i % len(users)],
                )
                for i in range(45)
            ]
        )
        covered["system/upload"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] upload 跳过：{exc}")
    return users, depts, roles, posts


def _seed_system_extended(users, admin, covered: dict) -> None:
    """任务执行记录 / 个人访问令牌 / 数据权限 / 登录日志。"""
    from audit.services import UserLoginLog
    from identity.models import PersonalAccessToken
    from system.models import DataPermission
    from task.services import TaskExecution

    try:
        TaskExecution.objects.bulk_create(
            [TaskExecution(name=f"抽样任务{i}", creator=users[i % len(users)]) for i in range(45)]
        )
        covered["system/task"] = 45
        PersonalAccessToken.objects.bulk_create(
            [
                PersonalAccessToken(
                    name=f"令牌{i}", token_hash=f"hash-{i:04d}", token_prefix=f"tp{i:02d}", creator=admin
                )
                for i in range(45)
            ]
        )
        covered["system/token"] = 45
        DataPermission.objects.bulk_create(
            [DataPermission(name=f"数据权限{i}", rules=[], creator=users[i % len(users)]) for i in range(45)]
        )
        covered["system/permission"] = 45
        logs = []
        for i in range(45):
            log = UserLoginLog(creator=users[i % len(users)], ipaddress=f"10.0.0.{i % 250}", agent="sql-sampling")
            if i % 5 == 0:
                log.login_type = UserLoginLog.LoginTypeChoices.WEBSOCKET
                log.channel_name = f"ws-{i}"
            logs.append(log)
        UserLoginLog.objects.bulk_create(logs)
        covered["system/login-log"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] system 扩展表跳过：{exc}")


def _seed_notifications(users, admin, depts, roles, posts, covered: dict) -> None:
    """站内信（USER 型 40 条）+ 部门/角色/岗位公告各 1 条。"""
    try:
        from notifications.models import MessageContent

        for i in range(40):
            msg = MessageContent.objects.create(
                title=f"站内信样本{i}", message="正文", notice_type=MessageContent.NoticeChoices.USER, creator=admin
            )
            msg.notice_user.add(users[i % len(users)])
        for kind, related in (
            (MessageContent.NoticeChoices.DEPT, "notice_dept"),
            (MessageContent.NoticeChoices.ROLE, "notice_role"),
            (MessageContent.NoticeChoices.POST, "notice_post"),
        ):
            msg = MessageContent.objects.create(title=f"{kind}公告", message="正文", notice_type=kind, creator=admin)
            getattr(msg, related).add(
                depts[0]
                if kind == MessageContent.NoticeChoices.DEPT
                else (roles[0] if kind == MessageContent.NoticeChoices.ROLE else posts[0])
            )
        covered["notifications/message"] = 43
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] notifications 跳过：{exc}")


def _seed_dataset(users, admin, covered: dict) -> None:
    try:
        from dataset.models import Dataset, DynamicForm, DynamicFormSubmission

        for i in range(45):
            Dataset.objects.create(name=f"数据集{i}", bound_model="identity.UserInfo", creator=admin)
        forms = [DynamicForm.objects.create(name=f"表单{i}", schema=[], creator=admin) for i in range(2)]
        DynamicFormSubmission.objects.bulk_create(
            [DynamicFormSubmission(form=forms[0], data={"k": i}, creator=users[i % len(users)]) for i in range(45)]
        )
        covered["dataset"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] dataset 跳过：{exc}")


def _seed_approval(users, admin, covered: dict) -> None:
    """流程 + 实例 + 请假单 + 操作审批（ApprovalRequest）+ 审批规则。"""
    try:
        from approval.models.approval import ApprovalFlow, ApprovalFlowNode
        from approval.services.approval_flow import create_instance

        flows = []
        for i in range(45):
            flow = ApprovalFlow.objects.create(
                name=f"抽样流程{i}", code=f"sql_sampling_{i}", form_schema=[], is_active=True
            )
            ApprovalFlowNode.objects.create(
                flow=flow,
                name="初审",
                order=1,
                approve_type=ApprovalFlowNode.ApproveType.OR,
                assignee_type=ApprovalFlowNode.AssigneeType.USER,
                assignee_value=admin.username,
                condition={},
                timeout_hours=0,
            )
            flows.append(flow)
        instances = []
        for i in range(45):
            instance, error = create_instance(
                flow=flows[i], applicant=users[i % len(users)], title=f"抽样申请{i}", form_data={}
            )
            if error is None:
                instances.append(instance)
        covered["approval/instance"] = len(instances)

        try:
            from datetime import timedelta

            from django.utils import timezone

            from approval.models.leave import Leave

            base = timezone.localdate()
            for instance in instances:
                Leave.objects.create(
                    instance=instance,
                    start_date=base,
                    end_date=base + timedelta(days=1),
                    days=1,
                    reason="抽样",
                    creator=instance.creator,
                )
            covered["approval/leave"] = len(instances)
        except Exception as exc:  # noqa: BLE001
            print(f"  [seed] leave 跳过：{exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] approval 跳过：{exc}")
        return

    try:
        from approval.models.approval_request import ApprovalRequest
        from approval.models.approval_rule import ApprovalRule

        for i in range(45):
            ApprovalRule.objects.create(name=f"审批规则{i}", path_patterns=["^api/"], creator=admin)
        requests_ = [
            ApprovalRequest(method="GET", path=f"api/identity/user/{i}", approver=admin, creator=users[i % len(users)])
            for i in range(45)
        ]
        ApprovalRequest.objects.bulk_create(requests_)
        for request_ in requests_:
            request_.current_assignees.set([users[(request_.path.count("u") + 1) % len(users)], admin])
        covered["approval/request"] = 45
        covered["approval/rule"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] approval request/rule 跳过：{exc}")


def _seed_ai(admin, covered: dict) -> None:
    try:
        from ai.models.ai import AiKnowledgeDocument, AiProfile
        from ai.models.mcp import McpServer

        for i in range(45):
            AiProfile.objects.create(name=f"模型画像{i}", base_url="https://api.example.com/v1", creator=admin)
            McpServer.objects.create(name=f"MCP{i}", url="https://mcp.example.com", creator=admin)
            AiKnowledgeDocument.objects.create(path=f"kb/sample-{i:02d}.md", title=f"知识文档{i}", creator=admin)
        covered["ai"] = 135
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] ai 跳过：{exc}")


def seed() -> dict:
    """按抽样面播种代表量数据（块间防御式隔离：单块失败不阻断其余端点抽样）。

    返回各域播种行数（covered），随报告输出供评估数据充分性。
    """
    covered: dict[str, int] = {}
    admin = _ensure_admin()
    users, depts, roles, posts = _seed_system_core(admin, covered)
    _seed_system_extended(users, admin, covered)
    _seed_notifications(users, admin, depts, roles, posts, covered)
    _seed_dataset(users, admin, covered)
    _seed_approval(users, admin, covered)
    _seed_ai(admin, covered)
    return covered
